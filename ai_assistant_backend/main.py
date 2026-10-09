import asyncio
import json
import logging
import os
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select, update

from auth import create_access_token, get_current_user
from agent.chat_model import (
    DEFAULT_MODEL_NAME,
    ChatAgent,
    checkpointer,
    is_supported_model,
    list_models,
)
from config.pgsql.pgvector_client import create_collection
from knowledge import router as knowledge_router
from model.agent_model import *
from model.knowledge_base_model import KnowledgeBase
from model.user_model import *
from model.user_model import *
from model.user_model import UserVO
from config.pgsql.pgsql_connection import *

load_dotenv()

logger = logging.getLogger("app")

app = FastAPI(lifespan=lifespan)

# 跨域：前端独立部署（Vite dev 5173 / 静态站点 / 其他域名）时浏览器需要 CORS 头。
# 鉴权走 Authorization: Bearer 头而不是 Cookie，所以不开启 allow_credentials，
# 这样 allow_origins 用 "*" 也不会被浏览器拒绝（带 Cookie 时 "*" 是无效的）。
# 生产环境建议在 .env 里用 CORS_ORIGINS 收敛为具体域名，逗号分隔。
_cors_raw = (os.getenv("CORS_ORIGINS") or "*").strip()
CORS_ORIGINS = ["*"] if _cors_raw == "*" else [item.strip() for item in _cors_raw.split(",") if item.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(knowledge_router)

@app.post("/users/register")
async def register_users(user: UserRegisterDto,db: AsyncSession = Depends(get_db)):
    # id 由 User 模型的 default=uuid4 生成
    user_obj = User(**user.model_dump())
    db.add(user_obj)
    await db.commit()
    await db.refresh(user_obj)  # 刷新对象，获取数据库生成的主键等信息

    # 为新用户初始化一个私人知识库：langchain 侧登记集合，本地表记录归属。
    # 集合创建失败不影响注册结果，只是该用户暂时没有私人库，可后续补建。
    collection_name = f"user_{user_obj.id}"
    try:
        await run_in_threadpool(create_collection, collection_name)
        db.add(KnowledgeBase(
            name="我的知识库",
            owner_id=user_obj.id,
            collection_name=collection_name,
        ))
        await db.commit()
    except Exception:
        await db.rollback()

    return BaseResponse(code=200, message="注册成功")

@app.post("/users/login")
async def login_user(user: UserLoginDto, db: AsyncSession = Depends(get_db)):
    account = user.account
    # UserLoginDto 没有哈希校验器，这里的 password 是明文
    password = user.password
    result = await db.execute(select(User).where(User.account == account))
    current_user = result.scalar_one_or_none()
    if not current_user:
        raise HTTPException(status_code=401,detail="用户名或者密码错误")
    db_password = current_user.password
    consequence = verify_password(password, db_password)
    if not consequence:
        raise HTTPException(status_code=401,detail="用户名或者密码错误")
    # 登录成功的标志就是发一个 token。之后前端每次请求带着它，
    token = create_access_token(current_user.id, current_user.token_version)
    return BaseResponse(code=200, message="登录成功",
                        data=LoginVO(token=token,
                                     user=UserVO.model_validate(current_user)))


@app.get("/users/me")
async def get_me(current_user: User = Depends(get_current_user)):
    """验证登录状态的接口：带着 Authorization: Bearer <token> 请求。
    能正常返回就是已登录；没带 token / token 无效 / 已过期 -> 401。
    """
    return BaseResponse(code=200, message="获取成功",
                        data=UserVO.model_validate(current_user))


@app.post("/users/logout")
async def logout(current_user: User = Depends(get_current_user),
                 db: AsyncSession = Depends(get_db)):
    """退出登录：使用版本号法使该用户当前所有已签发的 token 立即失效。
    """
    current_user.token_version += 1
    await db.commit()
    return BaseResponse(code=200, message="已退出登录")

@app.post("/conversation/add")
async def add_conversation(
    conversation: ConversationDto,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conversation_obj = Conversation(user_id=current_user.id, title=conversation.title)
    db.add(conversation_obj)
    await db.commit()
    await db.refresh(conversation_obj)  # 刷新对象，获取数据库生成的主键等信息
    return BaseResponse(code=200, message="会话添加成功",
                        data=ConversationVO.model_validate(conversation_obj))

def _sse(event: str, data: dict) -> str:
    """SSE 帧。data 走 JSON，前端按 event 分发。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _next_sequence(db: AsyncSession, conversation_id) -> int:
    """会话内下一个消息序号。user / assistant 两条要连续，抽出来共用。"""
    last = (await db.execute(
        select(ConversationMessage.sequence)
        .where(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.sequence.desc())
        .limit(1)
    )).scalar_one_or_none()
    return (last or 0) + 1


async def _load_own_conversation(db: AsyncSession, conversation_id, current_user):
    """校验会话存在、属于当前用户、且未被删除。

    只查 id 不够：知道别人会话 id 的人就能往那个会话里写消息。
    不存在和不属于自己返回同一个 404，避免靠状态码差异探测别人的会话是否存在。
    """
    conversation = (await db.execute(
        select(Conversation).where(Conversation.id == conversation_id,
                                   Conversation.user_id == current_user.id,
                                   Conversation.is_deleted == False)
    )).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conversation


def _resolve_model(model_name: str | None) -> str:
    """校验前端传来的模型名。

    不校验的话，ChatAgent.get_agent 抛的 ValueError 没人接，会变成 500；
    校验后返回 400 并带上可选清单，前端能直接提示。
    """
    if not model_name:
        return DEFAULT_MODEL_NAME
    if not is_supported_model(model_name):
        raise HTTPException(
            status_code=400,
            detail=f"不支持的模型: {model_name}，可选: " + ", ".join(m["name"] for m in list_models()))
    return model_name


@app.get("/model/get")
async def get_models(current_user: User = Depends(get_current_user)):
    """可用模型清单，供前端渲染模型下拉；默认项用 is_default 标记。

    名单维护在 agent/chat_model.py，前端不要自己硬编码一份，
    否则以后加模型两边会不一致。
    """
    return BaseResponse(code=200, message="获取成功", data=list_models())


@app.post("/conversation/chat")
async def generate_chat(request: ChatRequest,
                        current_user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    # 会话必须存在、属于当前用户、且未被删除。
    await _load_own_conversation(db, request.session_id, current_user)
    model_name = _resolve_model(request.model_name)

    question = request.query
    session_id = request.session_id
    collection_name = f"user_{current_user.id}"
    chat_agent = ChatAgent(model_name=model_name, chat_id=str(session_id))
    # chat() 内部是同步的模型调用，必须丢线程池 ——
    # 直接在协程里调会把整个事件循环占死，其它请求全排队。
    response = await run_in_threadpool(
        chat_agent.chat, question,
        rag_flag=request.rag_flag, collection_name=collection_name)

    last = response["messages"][-1]
    # token_usage 不一定有（部分 OpenAI 兼容网关不返回），取不到就存 None，别让 KeyError 把接口打挂
    usage = (last.response_metadata or {}).get("token_usage") or {}
    sequence = await _next_sequence(db, session_id)
    db.add(ConversationMessage(conversation_id=session_id, role="user", content=question,
                               sequence=sequence, status="done", model=model_name,
                               token_count=usage.get("prompt_tokens")))
    db.add(ConversationMessage(conversation_id=session_id, role="assistant", content=last.content,
                               sequence=sequence + 1, status="done", model=model_name,
                               token_count=usage.get("completion_tokens")))
    await db.commit()
    return BaseResponse(code=200, message="聊天生成成功", data=last.content)


@app.post("/conversation/chat/stream")
async def generate_chat_stream(request: ChatRequest,
                               current_user: User = Depends(get_current_user),
                               db: AsyncSession = Depends(get_db)):
    """流式对话（SSE 逐段下发）。

    与 /conversation/chat 的区别只在传输方式，校验、检索、落库口径保持一致。

    落库时序：先插 user 消息 + assistant 占位(status=pending)，流结束后补齐；
    中断或报错则置 status=error 并**保留已生成的部分** ——
    否则界面显示的内容和库里存的会对不上。

    所有校验必须在 return StreamingResponse 之前做完：一旦开始吐响应体，
    再抛 HTTPException 已经改不了状态码了，只会变成流里的一段脏数据。
    """
    await _load_own_conversation(db, request.session_id, current_user)
    model_name = _resolve_model(request.model_name)

    question = request.query
    session_id = request.session_id
    collection_name = f"user_{current_user.id}"

    # 先把提问落库：客户端中途断开时，至少用户问过什么不会丢
    sequence = await _next_sequence(db, session_id)
    assistant_id = uuid4()
    db.add(ConversationMessage(conversation_id=session_id, role="user", content=question,
                               sequence=sequence, status="done", model=model_name))
    db.add(ConversationMessage(id=assistant_id, conversation_id=session_id, role="assistant",
                               content="", sequence=sequence + 1, status="pending",
                               model=model_name))
    await db.commit()

    chat_agent = ChatAgent(model_name=model_name, chat_id=str(session_id))

    async def _persist_assistant(content: str, status: str, usage: dict):
        """把助手消息补齐。另开 Session：请求级的 db 依赖跟流式响应的生命周期不重合，
        在生成器里继续用它是不安全的。"""
        async with AsyncSession(connection, expire_on_commit=False) as session:
            await session.execute(
                update(ConversationMessage)
                .where(ConversationMessage.id == assistant_id)
                .values(content=content, status=status,
                        token_count=(usage or {}).get("output_tokens"))
            )
            await session.commit()

    async def event_stream():
        parts: list[str] = []
        usage: dict = {}
        status = "done"
        try:
            yield _sse("start", {"message_id": str(assistant_id), "model": model_name})
            async for item in chat_agent.chat_stream(
                question, rag_flag=request.rag_flag, collection_name=collection_name
            ):
                if item["type"] == "token":
                    parts.append(item["text"])
                    yield _sse("token", {"t": item["text"]})
                elif item["type"] == "usage":
                    usage = item.get("usage") or {}
                elif item["type"] == "error":
                    # 交给下面的 except 统一处理，避免两条错误路径各写一遍
                    raise RuntimeError(item.get("message") or "生成失败")
        except asyncio.CancelledError:
            # 用户点「停止」或客户端断开时 Starlette 会取消这个生成器。
            # CancelledError 不是 Exception 的子类，必须单独接，否则收尾逻辑接不到它。
            status = "error"
            raise
        except Exception:
            # 不能只吞掉：流式下异常没法变成状态码，至少要留下日志，否则线上无从排查
            logger.exception("流式生成失败 conversation=%s", session_id)
            status = "error"
            yield _sse("error", {"message": "生成失败，请稍后重试"})
        else:
            yield _sse("done", {"message_id": str(assistant_id), "status": status,
                                "length": len("".join(parts))})
        finally:
            # 正常结束 / 生成报错 / 客户端断开，三条路都必须落库 ——
            # 漏了中断这条，助手消息会永远停在 pending 且正文为空。
            # 用 shield 保护：外层协程已被取消时，普通 await 会立刻再抛一次取消，落库就白做了。
            content = "".join(parts)
            try:
                await asyncio.shield(_persist_assistant(content, status, usage))
            except asyncio.CancelledError:
                pass  # 落库任务已经起跑，让它自己跑完
            except Exception:
                logger.exception("流式收尾落库失败 conversation=%s", session_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        # X-Accel-Buffering: 让 Nginx 之类的反代别缓冲，否则 token 会被攒着一起发
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@app.get("/conversation/list")
async def list_conversations(current_user: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Conversation).where(Conversation.user_id == current_user.id,
                                                           Conversation.is_deleted == False).order_by(Conversation.created_at.desc()))
    conversations = result.scalars().all()
    return BaseResponse(code=200, message="获取成功",
                        data=[ConversationVO.model_validate(c) for c in conversations])

@app.get("/conversation/messages/list")
async def list_conversations_messages(conversation_id: UUID,
                             current_user: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_db)):
    # 会话必须存在、属于当前用户、且未被删除。
    conversation = (await db.execute(
        select(Conversation).where(Conversation.id == conversation_id,
                                   Conversation.user_id == current_user.id,
                                   Conversation.is_deleted == False)
    )).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    result = await db.execute(select(ConversationMessage).where(ConversationMessage.conversation_id == conversation_id).order_by(ConversationMessage.sequence.asc()))
    messages = result.scalars().all()
    return BaseResponse(code=200, message="获取成功",
                        data=[ConversationMessageVO.model_validate(m) for m in messages])


@app.delete("/conversation/{conversation_id}")
async def delete_conversation(conversation_id: UUID,
                              current_user: User = Depends(get_current_user),
                              db: AsyncSession = Depends(get_db)):
    """删除会话：连同它下面的消息一起删掉，不可恢复。

    归属校验与 /conversation/chat 一致 —— 只按 id 删的话，知道别人会话 id 的人
    就能把别人的会话删掉。不存在 / 不属于自己 / 已删除统一返回同一个 404，
    避免靠状态码差异探测别人的会话是否存在。

    这里用硬删除而不是把 is_deleted 置为 true：聊天记录可能含敏感内容，
    "删了但还留在库里"没有意义；而且消息列表接口并没有过滤 is_deleted，
    软删也起不到隐藏效果。
    """
    conversation = (await db.execute(
        select(Conversation).where(Conversation.id == conversation_id,
                                   Conversation.user_id == current_user.id,
                                   Conversation.is_deleted == False)
    )).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 顺序不能反：conversation_messages.conversation_id 的外键没有 ON DELETE CASCADE，
    # 先删父表会撞 ForeignKeyViolation。
    # 消息之间还有 parent_id 自引用，同一条 DELETE 语句内互相引用是安全的
    # （NO ACTION 在语句结束时才校验）。
    messages_result = await db.execute(
        delete(ConversationMessage).where(ConversationMessage.conversation_id == conversation_id)
    )
    await db.execute(delete(Conversation).where(Conversation.id == conversation_id))
    await db.commit()

    # 会话正文在 langgraph 的 checkpointer 里还存了一份（按 thread_id 索引）。
    # 不做这步的话"删除"只删了一半，聊天内容仍留在 checkpoints / checkpoint_blobs 里。
    # 用官方 API，不要手写 DELETE —— 内部表结构由 langgraph 维护，版本升级可能变。
    # 注意必须用同步的 delete_thread：PostgresSaver 是同步实现，
    # adelete_thread() 在基类里直接 raise NotImplementedError。它是阻塞调用，放线程池。
    # 放在主删除之后并单独兜异常：清理失败不该把已经删掉的会话回滚回来。
    checkpoint_cleared = True
    try:
        await run_in_threadpool(checkpointer.delete_thread, str(conversation_id))
    except Exception:
        checkpoint_cleared = False

    return BaseResponse(code=200, message="会话已删除", data={
        "id": str(conversation_id),
        "deleted_messages": messages_result.rowcount or 0,
        "checkpoint_cleared": checkpoint_cleared,
    })

