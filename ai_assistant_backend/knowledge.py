import hashlib
import os
import tempfile
from datetime import datetime, timezone
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import cast, func, or_, select, Integer, String, Uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func
from model.knowledge_base_model import KnowledgeBase
from auth import get_current_user, require_admin
from config.minio.minio_client import BUCKET, client
from config.pgsql.pgsql_connection import get_db
from config.pgsql.pgvector_client import create_collection, store_for
from model.agent_model import BaseResponse
from model.knowledge_model import KnowledgeFile
from model.user_model import User
from model.vector_model import LangchainEmbedding
from rag.loader import load_and_split, load_only
from model.vector_model import LangchainEmbedding,LangchainCollection
router = APIRouter(prefix="/knowledge", tags=["knowledge"])


async def get_public_kb(db: AsyncSession) -> KnowledgeBase | None:
    """公共知识库：owner_id 为空且未删除（启动时由 lifespan 创建）。

    定义见 model/knowledge_base_model.py：owner_id 为空即公共库。
    """
    return (await db.execute(
        select(KnowledgeBase)
        .where(KnowledgeBase.owner_id.is_(None), KnowledgeBase.is_deleted == False)
        .order_by(KnowledgeBase.created_at)
        .limit(1)
    )).scalar_one_or_none()


async def get_or_create_private_kb(db: AsyncSession, user: User) -> KnowledgeBase:
    """取用户的私人知识库；不存在则补建。

    正常路径由 main.py 注册接口创建，这里是兜底：历史数据或注册时集合创建失败
    的用户，不应该在上传时直接 404。补建方式与注册保持一致 —— 先在 langchain
    侧登记集合，再落库记录归属。
    """
    knowledge_base = (await db.execute(
        select(KnowledgeBase).where(KnowledgeBase.owner_id == user.id)
    )).scalar_one_or_none()
    if knowledge_base is not None:
        return knowledge_base

    collection_name = f"user_{user.id}"
    await run_in_threadpool(create_collection, collection_name)
    knowledge_base = KnowledgeBase(
        name="我的知识库",
        owner_id=user.id,
        collection_name=collection_name,
    )
    db.add(knowledge_base)
    await db.commit()
    await db.refresh(knowledge_base)
    return knowledge_base


class IngestSource:
    """入库流程需要的字段快照。

    不能直接把 KnowledgeFile 对象传进线程池，有两个原因：
    1. Session 不是线程安全的，ORM 对象脱离创建它的会话使用本身就不可靠；
    2. ORM 属性可能在 commit() 后过期，在工作线程里访问会触发同步懒加载，
       直接抛 sqlalchemy.exc.MissingGreenlet（greenlet_spawn has not been called）。

    所以调用方在异步上下文里先把需要的字段摘出来，再交给 _ingest。
    """

    __slots__ = ("id", "original_name", "bucket", "object_key", "content_type")

    def __init__(self, record: KnowledgeFile) -> None:
        self.id = record.id
        self.original_name = record.original_name
        self.bucket = record.bucket
        self.object_key = record.object_key
        self.content_type = record.content_type


def _with_downloaded_file(source: IngestSource, handler):
    """把对象存储里的文件下到临时文件，交给 handler 处理后删掉临时文件。

    同步阻塞（含文件 IO 与解析），调用方需放进线程池。
    下载与临时文件的收尾只写这一处：入库和预览共用，免得两边清理逻辑走偏、
    在磁盘上留下临时文件。
    """
    #  把一个文件名从最后一个点切开，分成「文件名」和「扩展名」两部分，返回一个二元组。
    suffix = os.path.splitext(source.original_name)[1]
    tmp_path = None
    try:
        # NamedTemporaryFile 生成没有扩展名的临时文件
        # delete控制临时文件在关闭时要不要被自动删除，True为自动删除，False为不自动删除
        # suffix指定后缀
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = tmp.name
            response = client.get_object(source.bucket, source.object_key)
            try:
                for chunk in response.stream(64 * 1024):
                    tmp.write(chunk)
            finally:
                response.close()
                response.release_conn()

        return handler(tmp_path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def _download_and_split(source: IngestSource):
    """下载 → 解析切分，返回文本段。入库流程用。"""
    return _with_downloaded_file(
        source, lambda path: load_and_split(path, source.content_type)
    )


def _download_and_parse(source: IngestSource):
    """下载 → 解析但**不切分**，返回原始文档。预览流程用。"""
    return _with_downloaded_file(
        source, lambda path: load_only(path, source.content_type)
    )


def _extract_preview(source: IngestSource, max_chars: int) -> tuple[str, int]:
    """取正文前若干字，返回 (预览文本, 全文字数)。

    **取的是未切分的原始文本**，不是分段拼接：分段之间带 chunk_overlap，
    把分段拼回去会在每个接缝重复几十字（实测 overlap=50 时约 40 字/接缝），
    审核员读起来会以为文档自己重复了一段。分段是写入向量库的存储细节，
    判断内容是否合规则应该看原文。

    **只解析、不写向量库** —— 审核阶段还不该产生任何分段，
    所以不能复用 _ingest。
    """
    documents = _download_and_parse(source)
    full = "\n\n".join(doc.page_content for doc in documents)
    return full[:max_chars], len(full)


def _ingest(source: IngestSource, collection_name: str | None = None) -> int:
    """从 MinIO 取文件、解析切分、写入向量库。返回写入的文本段数。

    同步阻塞（含文件 IO、解析、向量化），调用方需放进线程池。
    任何异常都向上抛，由调用方把状态置为 failed。
    """
    chunks = _download_and_split(source)
    # 这些写进 cmetadata，供列表查询返回：
    # file_id 用于关联文件，seq 是段序号，ingested_at 是入库时间
    ingested_at = datetime.now(timezone.utc).isoformat()
    for index, chunk in enumerate(chunks):
        chunk.metadata["file_id"] = str(source.id)
        chunk.metadata["source"] = source.original_name
        chunk.metadata["seq"] = index
        chunk.metadata["ingested_at"] = ingested_at
    if chunks:
        if not collection_name:
            store_for("public").add_documents(chunks)
        else:
            store_for(collection_name).add_documents(chunks)
    return len(chunks)


def _file_to_dict(record: KnowledgeFile, uploader: tuple | None = None,
                  reviewer: tuple | None = None) -> dict:
    """把 KnowledgeFile 转成纯 dict。

    BaseResponse.data 声明为 Any，直接塞 ORM 对象的话，FastAPI 在响应序列化
    阶段会抛 PydanticSerializationError: Unable to serialize unknown type
    （Pydantic 不认识 SQLAlchemy 模型）。列表为空时不会暴露，一旦有数据就 500。
    时间统一用 isoformat，和之前 FastAPI 自动序列化的结果保持一致。

    uploader / reviewer 都是 (username, account) 二元组，由调用方批量查好后传进来 ——
    审核要认出「谁传的、谁审的」，光给一串 UUID 没有用。
    这里不自己去查库：它是纯转换函数，进不去数据库，也避免逐行查询（N+1）。
    """
    uploader_name, uploader_account = uploader if uploader else (None, None)
    reviewer_name, reviewer_account = reviewer if reviewer else (None, None)
    return {
        "id": str(record.id),
        "knowledge_base_id": str(record.knowledge_base_id) if record.knowledge_base_id else None,
        "uploader_id": str(record.uploader_id) if record.uploader_id else None,
        # 给前端直接显示用的人名，不用再自己拿 id 去换
        "uploader_name": uploader_name,
        "uploader_account": uploader_account,
        "original_name": record.original_name,
        "object_key": record.object_key,
        "bucket": record.bucket,
        "content_type": record.content_type,
        "size": record.size,
        "file_hash": record.file_hash,
        "status": record.status,
        "reviewer_id": str(record.reviewer_id) if record.reviewer_id else None,
        "reviewer_name": reviewer_name,
        "reviewer_account": reviewer_account,
        "review_comment": record.review_comment,
        "reviewed_at": record.reviewed_at.isoformat() if record.reviewed_at else None,
        "error_message": record.error_message,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "is_deleted": record.is_deleted,
    }

# 单文件上限 50 MB
MAX_SIZE = 50 * 1024 * 1024

# 允许的类型。不要只信文件名后缀，以 content_type 为准（进阶可用魔数校验）
ALLOWED_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/markdown",
    "text/csv",
}


# ---------- 删除 / 替换共用 ----------

def _purge_segments_sync(collection_name: str, segment_ids: list[str]) -> int:
    """删除向量分段，返回删除条数。

    同步阻塞（要连库），调用方必须放进线程池。
    用 PGVector.delete(ids=[...])，它按 langchain_pg_embedding 的主键删；
    分段 id 全局唯一，所以不需要按集合过滤。
    """
    if not segment_ids:
        return 0
    store_for(collection_name).delete(ids=segment_ids)
    return len(segment_ids)


async def _file_segment_ids(db: AsyncSession, file_id) -> list[str]:
    """某份文件在向量库里的全部分段 id。

    只按 cmetadata 里的 file_id 过滤：它是 knowledge_files 的主键 UUID、全局唯一，
    不必再限定集合。cmetadata 是 JSONB，取值要 .astext。
    """
    return list((await db.execute(
        select(LangchainEmbedding.id)
        .where(LangchainEmbedding.cmetadata["file_id"].astext == str(file_id))
    )).scalars().all())


async def _purge_file(db: AsyncSession, record: KnowledgeFile) -> dict:
    """把一份文件从三处一起清掉：向量分段 → 文件记录（软删）→ MinIO 对象。

    只清理不 commit，事务边界交给调用方（删除接口与替换流程复用）。

    顺序有讲究：向量分段是「活」数据、会被检索命中，必须先删；
    万一后面失败，文件记录还留在列表里，用户看得见也能重试。
    反过来先删记录、而分段残留的话，那些分段就成了无法管理的孤儿，还在被检索。

    MinIO 删除失败既不回滚也不中断 —— 记录与分段已经清干净，
    对象变成无人引用的孤儿只是占空间，如实回一个 storage_removed=false 即可。
    """
    knowledge_base = (await db.execute(
        select(KnowledgeBase).where(KnowledgeBase.id == record.knowledge_base_id)
    )).scalar_one_or_none()
    collection_name = knowledge_base.collection_name if knowledge_base else "public"

    segment_ids = await _file_segment_ids(db, record.id)
    deleted_segments = await run_in_threadpool(_purge_segments_sync, collection_name, segment_ids)

    # 软删，与项目其它表（knowledge_bases / conversations）保持同一口径
    record.is_deleted = True

    storage_removed = True
    try:
        await run_in_threadpool(client.remove_object, record.bucket, record.object_key)
    except Exception:
        storage_removed = False

    return {"deleted_segments": deleted_segments, "storage_removed": storage_removed}


async def _load_owned_file(db: AsyncSession, file_id: UUID, current_user: User) -> KnowledgeFile:
    """取出文件记录并做归属校验。

    公共库文件仅管理员可操作；私人库文件只有库主本人（或管理员）可操作。
    「不存在 / 已删除 / 无权操作」统一返回同一个 404 ——
    用 403 会让外人靠状态码差异探测出「这个 id 确实存在」。
    """
    record = (await db.execute(
        select(KnowledgeFile).where(KnowledgeFile.id == file_id,
                                   KnowledgeFile.is_deleted == False)
    )).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="文件不存在")

    if current_user.role == "admin":
        return record

    knowledge_base = (await db.execute(
        select(KnowledgeBase).where(KnowledgeBase.id == record.knowledge_base_id)
    )).scalar_one_or_none()
    if knowledge_base is None or knowledge_base.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="文件不存在")
    return record


async def _find_same_name_files(db: AsyncSession, knowledge_base_id,
                                original_name: str) -> list[KnowledgeFile]:
    """同一知识库内、未删除的同名文件。

    只比 original_name，且限定在同一知识库范围：私人库与公共库的同名文件互不影响。
    改了名的同一份文档（如「手册v2.pdf」）不会命中、仍会并存 ——
    用户主动改名就不该被自动替换，这是有意的。
    """
    rows = (await db.execute(
        select(KnowledgeFile).where(KnowledgeFile.knowledge_base_id == knowledge_base_id,
                                   KnowledgeFile.original_name == original_name,
                                   KnowledgeFile.is_deleted == False)
    )).scalars().all()
    return list(rows)


@router.post("/admin/upload")
async def admin_upload_file(
    file: UploadFile = File(...),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """上传文件到 MinIO，并写一条 status=pending 的记录等待审核。"""
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {file.content_type}")

    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(status_code=400, detail="文件超过 50MB 限制")
    if not content:
        raise HTTPException(status_code=400, detail="文件内容为空")

    file_hash = hashlib.sha256(content).hexdigest()
    # uuid 前缀保证对象名唯一，避免不同用户的同名文件互相覆盖
    object_key = f"knowledge/{uuid4()}/{file.filename}"

    # MinIO 的 put_object 是同步阻塞调用，放到线程池里以免卡住事件循环
    import io
    await run_in_threadpool(
        client.put_object,
        BUCKET,
        object_key,
        io.BytesIO(content),
        len(content),
        file.content_type,
    )

    # 公共库归属必须写进 knowledge_base_id，否则公共和私人文件都退化成 NULL，
    # 既查不到私人文件，私人文件还会混进管理员的公共审核队列。
    public_kb = await get_public_kb(db)

    record = KnowledgeFile(
        knowledge_base_id=public_kb.id if public_kb else None,
        uploader_id=current_user.id,
        original_name=file.filename,
        object_key=object_key,
        bucket=BUCKET,
        content_type=file.content_type,
        size=len(content),
        file_hash=file_hash,
        status="pending",
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return BaseResponse(code=200, message="上传成功，等待审核", data={
        "id": str(record.id),
        "original_name": record.original_name,
        "size": record.size,
        "status": record.status,
    })

@router.post("/user/upload")
async def user_upload_file(
    file: UploadFile = File(...),
    replace: bool = Form(False),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传文件到当前用户的私人知识库，上传即解析入库（无需审核）。

    同名冲突**不会静默覆盖**：
      - replace=False 且已存在同名 → 409，把冲突交回前端确认。
        （前端正常情况下会先调 /file/conflict 探一次，这个 409 是服务端兜底，
        防止别的调用方绕过确认直接覆盖。）
      - replace=True → 走「先写新的、确认入库成功后再清旧的」，任何时刻都不会两头空。
    """
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {file.content_type}")

    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(status_code=400, detail="文件超过 50MB 限制")
    if not content:
        raise HTTPException(status_code=400, detail="文件内容为空")

    # 先确定目标知识库，再往 MinIO 写 —— 否则知识库缺失时会在 MinIO 里
    # 留下一个没有业务归属的孤儿对象。
    knowledge_base = await get_or_create_private_kb(db, current_user)
    file_hash = hashlib.sha256(content).hexdigest()

    conflicts = await _find_same_name_files(db, knowledge_base.id, file.filename)
    if conflicts and not replace:
        raise HTTPException(status_code=409,
                            detail=f"已存在同名文档《{file.filename}》，请确认是否替换")

    # 内容没变就不必再跑一遍解析 + 向量化（embedding 是外部调用，整条链路最贵的一步）。
    # 只在旧记录已入库(stored)时才跳：failed 的没有分段，重传正是为了补救它。
    unchanged = next((c for c in conflicts
                      if c.status == "stored" and c.file_hash == file_hash), None)
    if unchanged is not None:
        return BaseResponse(code=200, message="内容与已有文档一致，未重复入库", data={
            "id": str(unchanged.id),
            "status": unchanged.status,
            "skipped": True,
            "replaced": 0,
        })

    # 旧的先只记下来，等新文档确认入库成功后再清（方案 B，理由见下面的注释）
    stale_ids = [c.id for c in conflicts]

    # uuid 前缀保证对象名唯一，避免不同用户的同名文件互相覆盖
    object_key = f"knowledge/{uuid4()}/{file.filename}"

    # MinIO 的 put_object 是同步阻塞调用，放到线程池里以免卡住事件循环
    import io
    await run_in_threadpool(
        client.put_object,
        BUCKET,
        object_key,
        io.BytesIO(content),
        len(content),
        file.content_type,
    )
    record = KnowledgeFile(
        knowledge_base_id=knowledge_base.id,
        uploader_id=current_user.id,
        original_name=file.filename,
        object_key=object_key,
        bucket=BUCKET,
        content_type=file.content_type,
        size=len(content),
        file_hash=file_hash,
        status="approved",
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    # 入库所需字段先快照，后面不再把 ORM 对象带进线程池
    source = IngestSource(record)
    file_id = record.id

    # 私人库由本人上传，等价于「已通过审核」，直接把审核人记成本人
    record.reviewer_id = current_user.id
    record.reviewed_at = datetime.now(timezone.utc)
    await db.commit()

    # 解析入库。放线程池里执行，避免阻塞事件循环。
    # 失败不让上传接口报 500：文件记录已落库，解析失败单独记为 failed。
    try:
        count = await run_in_threadpool(_ingest, source, knowledge_base.collection_name)
        record.status = "stored"
        message = f"已入库 {count} 个文本段"
    except Exception as e:
        record.status = "failed"
        record.error_message = str(e)[:500]
        message = "解析入库失败"
    await db.commit()

    # 方案 B：新文档确认入库成功之后，才清掉同名的旧的。
    # 反过来的话，下载解析或向量化一失败就变成「旧的没了、新的也没进去」——
    # embedding 要调外部服务，这个失败概率不低，不值得赌。
    replaced = 0
    if stale_ids and record.status == "stored":
        for stale_id in stale_ids:
            stale = (await db.execute(
                select(KnowledgeFile).where(KnowledgeFile.id == stale_id)
            )).scalar_one_or_none()
            if stale is not None:
                await _purge_file(db, stale)
                replaced += 1
        await db.commit()

    if stale_ids and record.status != "stored":
        # 新文件失败时旧文档完好，明确告诉用户，别让他以为已经覆盖成功
        message = f"{message}；原文档未受影响，仍然可用"

    return BaseResponse(code=200, message=message, data={
        "id": str(file_id),
        "status": record.status,
        "replaced": replaced,
        "skipped": False,
    })


@router.get("/file/conflict")
async def check_file_conflict(
    name: str = Query(..., description="待上传的原始文件名"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传前探测同名冲突，供前端决定要不要弹「是否替换」。

    只查当前用户自己的私人知识库 —— 公共库有审核流程，不做自动替换。
    没有私人库时直接回「无冲突」，不报 404（否则页面加载路径会被打断）。
    """
    knowledge_base = (await db.execute(
        select(KnowledgeBase).where(KnowledgeBase.owner_id == current_user.id)
    )).scalar_one_or_none()
    if knowledge_base is None:
        return BaseResponse(code=200, message="获取成功", data={"conflict": False, "files": []})

    rows = await _find_same_name_files(db, knowledge_base.id, name)
    return BaseResponse(code=200, message="获取成功", data={
        "conflict": bool(rows),
        "files": [{
            "id": str(r.id),
            "original_name": r.original_name,
            "status": r.status,
            "size": r.size,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows],
    })


@router.delete("/file/{file_id}")
async def delete_file(
    file_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除一份文档：向量分段、文件记录、MinIO 对象三处一起清，不可恢复。

    权限见 _load_owned_file：公共库文件仅管理员，私人库文件本人或管理员。
    """
    record = await _load_owned_file(db, file_id, current_user)
    original_name = record.original_name
    result = await _purge_file(db, record)
    await db.commit()

    removed = result["deleted_segments"]
    return BaseResponse(
        code=200,
        message=f"已删除文档及其 {removed} 个文本段" if removed else "已删除文档",
        data={"id": str(file_id), "original_name": original_name, **result},
    )

@router.get("/file/{file_id}/preview")
async def preview_file(
    file_id: UUID,
    max_chars: int = Query(default=4000, ge=200, le=20000,
                           description="最多返回多少字的正文"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """审核前预览文档正文（抽取后的纯文本）。

    为什么需要它：**待审核的文件还没解析过**，向量库里没有它的分段，
    所以内容只能现从对象存储取。审核要判断的是「内容是否合规」，
    而元数据（文件名、大小、Content-Type）根本支撑不了这个判断。
    这里返回的就是「审核通过后将要进向量库的文字」。

    **解析失败不报 500，而是把原因回给前端**：这本身就是审核依据 ——
    连解析都失败，说明通过审核也只会得到一条 failed 记录，该走驳回。
    """
    record = await _load_owned_file(db, file_id, current_user)
    source = IngestSource(record)
    try:
        text, total_chars = await run_in_threadpool(_extract_preview, source, max_chars)
    except Exception as e:
        return BaseResponse(code=200, message="解析失败", data={
            "available": False,
            "error": str(e)[:300],
            "text": "",
            "total_chars": 0,
            "truncated": False,
        })

    return BaseResponse(code=200, message="获取成功", data={
        "available": True,
        "error": None,
        "text": text,
        "total_chars": total_chars,
        "truncated": total_chars > len(text),
    })


@router.get("/file/{file_id}/download")
async def download_file(
    file_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """下载原始文件（由后端从对象存储转发）。

    不用 MinIO 的 presigned URL：那要求**浏览器能直连 MinIO**，
    而 MinIO 通常只在服务端可达的网段里。走后端转发则不受网络拓扑影响，
    权限校验也仍然在应用这一层。
    """
    record = await _load_owned_file(db, file_id, current_user)
    obj = await run_in_threadpool(client.get_object, record.bucket, record.object_key)

    def iter_chunks():
        # 同步生成器：Starlette 会用 iterate_in_threadpool 消费，不会卡住事件循环
        try:
            for chunk in obj.stream(64 * 1024):
                yield chunk
        finally:
            obj.close()
            obj.release_conn()

    # 文件名可能含中文，用 RFC 5987 的 filename* 形式，避免 header 编码报错
    disposition = "attachment; filename*=UTF-8''%s" % quote(record.original_name)
    return StreamingResponse(
        iter_chunks(),
        media_type=record.content_type or "application/octet-stream",
        headers={"Content-Disposition": disposition},
    )


class ReviewRequest(BaseModel):
    approved: bool                 # True 通过，False 驳回
    comment: str | None = None     # 驳回时填写原因


@router.post("/review/{file_id}")
async def review_file(
    file_id: UUID,
    body: ReviewRequest,
    reviewer: User = Depends(require_admin),   # 仅 admin，自带登录校验
    db: AsyncSession = Depends(get_db),
):
    """审核文件。通过则置为 approved（等待后续解析入库），驳回则置为 rejected。"""
    record = (await db.execute(
        select(KnowledgeFile).where(KnowledgeFile.id == file_id,
                                    KnowledgeFile.is_deleted == False)
    )).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="文件不存在")

    # 只有待审核的能被审核，避免把已入库/已驳回的状态覆盖回去
    if record.status != "pending":
        raise HTTPException(status_code=409, detail=f"当前状态为 {record.status}，不能审核")

    record.status = "approved" if body.approved else "rejected"
    record.reviewer_id = reviewer.id
    record.review_comment = body.comment
    record.reviewed_at = datetime.now(timezone.utc)
    await db.commit()

    # 入库所需字段先快照，后面不再把 ORM 对象带进线程池
    source = IngestSource(record)

    # 审核通过后同步解析入库。放线程池里执行，避免阻塞事件循环。
    # 失败不让审核接口报 500：审核决定已落库，解析失败单独记为 failed。
    if body.approved:
        try:
            # 入到文件自己所属的知识库集合；历史数据可能没有归属，回退到公共集合
            kb = None
            if record.knowledge_base_id is not None:
                kb = (await db.execute(
                    select(KnowledgeBase).where(KnowledgeBase.id == record.knowledge_base_id)
                )).scalar_one_or_none()
            collection_name = kb.collection_name if kb is not None else "public"
            count = await run_in_threadpool(_ingest, source, collection_name)
            record.status = "stored"
            message = f"审核通过，已入库 {count} 个文本段"
        except Exception as e:
            record.status = "failed"
            record.error_message = str(e)[:500]
            message = "审核通过，但解析入库失败"
        await db.commit()
        await db.refresh(record)
    else:
        message = "已驳回"

    return BaseResponse(code=200, message=message, data={
        "id": str(record.id),
        "status": record.status,
    })

@router.get("/file/list")
async def list_files(
    page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页条数，由前端「每页 N 条」选择后传入"),
    scope: str = Query(default="private", description="列表范围：private（我的私人库）/ public（公共库，仅管理员）"),
    status: str | None = Query(default=None, description="按状态筛选：pending/approved/stored/rejected/failed"),
    keyword: str | None = Query(default=None, description="按文件名 / 对象路径 / 文件 id 模糊搜索"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """分页返回文件列表，单一口径 —— 由 scope 决定查私人库还是公共库。

    两个页面各取所需：我的知识库取 scope=private，导入审核取 scope=public。
    返回格式统一为 { total, page, page_size, items }，前端据此渲染分页器。

    分页、状态筛选、关键字搜索全部下推到数据库，不要先取全量再在前端切片。
    私人库：当前用户的私人知识库；用户还没有私人库时返回空列表，不报 404
    （新用户首次进入属正常情况，报错会让整页加载失败）。
    公共库：仅管理员可查（审核队列用）。
    """
    filters = [KnowledgeFile.is_deleted == False]

    if scope == "public":
        if current_user.role != "admin":
            raise HTTPException(status_code=403, detail="仅管理员可查看公共知识库文件")
        public_kb = await get_public_kb(db)
        if public_kb is None:
            return BaseResponse(code=200, message="获取成功", data={
                "total": 0, "page": page, "page_size": page_size, "scope": scope, "items": [],
            })
        filters.append(KnowledgeFile.knowledge_base_id == public_kb.id)
    else:
        knowledge_base = (await db.execute(
            select(KnowledgeBase).where(KnowledgeBase.owner_id == current_user.id)
        )).scalar_one_or_none()
        if knowledge_base is None:
            return BaseResponse(code=200, message="获取成功", data={
                "total": 0, "page": page, "page_size": page_size, "scope": scope, "items": [],
            })
        filters.append(KnowledgeFile.knowledge_base_id == knowledge_base.id)

    if status:
        filters.append(KnowledgeFile.status == status)
    if keyword and keyword.strip():
        pattern = f"%{_escape_like(keyword.strip())}%"
        filters.append(or_(
            KnowledgeFile.original_name.ilike(pattern, escape="\\"),
            KnowledgeFile.object_key.ilike(pattern, escape="\\"),
            cast(KnowledgeFile.id, String).ilike(pattern, escape="\\"),
        ))

    # total 与当页数据共用同一套 filters，保证口径一致
    total = (await db.execute(
        select(func.count(KnowledgeFile.id)).where(*filters)
    )).scalar_one()

    # 必须有稳定排序，否则分页会漏行或重复行
    rows = (await db.execute(
        select(KnowledgeFile)
        .where(*filters)
        .order_by(KnowledgeFile.created_at.desc(), KnowledgeFile.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).scalars().all()

    # 上传者姓名：一次批量查完，避免逐行查 users（N+1）。
    # 用 select(User.id, ...) 而不是 select(User)：只取需要的三列，
    # 顺带避免把 ORM 实体带出 Session。
    # 批量查一次人名（上传者 + 审核人），避免逐行查询（N+1）。
    # 页面上只显示姓名/账号，UUID 摆给审核员看没有意义。
    person_ids = {row.uploader_id for row in rows if row.uploader_id}
    person_ids |= {row.reviewer_id for row in rows if row.reviewer_id}
    people: dict = {}
    if person_ids:
        person_rows = (await db.execute(
            select(User.id, User.username, User.account).where(User.id.in_(person_ids))
        )).all()
        people = {row_id: (username, account)
                  for row_id, username, account in person_rows}

    return BaseResponse(code=200, message="获取成功", data={
        "total": total,
        "page": page,
        "page_size": page_size,
        "scope": scope,
        "items": [_file_to_dict(row, people.get(row.uploader_id), people.get(row.reviewer_id))
                  for row in rows],
    })
def _escape_like(value: str) -> str:
    """转义 ILIKE 的通配符，避免用户输入的 % / _ 被当成通配符导致全表命中。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/vector/list")
async def list_vectors(
    page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页条数，由前端「每页 N 条」选择后传入"),
    content_type: str | None = Query(default=None, description="按文档类型筛选，如 application/pdf"),
    segment_id: str | None = Query(default=None, description="按分段 id 精确查询"),
    keyword: str | None = Query(default=None, description="按分段正文 / 文档名 / 分段 id 模糊搜索"),
    document: str | None = Query(default=None, description="按来源文档名精确筛选"),
    scope: str | None = Query(default=None, description="来源范围：private / public，缺省为两者"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """分页返回知识库的文本段。

    **可见范围按角色区分**：管理员可看公共库 + 本人私人库；普通用户只能看本人私人库
    （强行传 scope=public 返回 403）。这与 /file/list 的口径一致。
    公共库语料仍然参与问答检索，只是不在这里对普通用户展示原文。

    分页、搜索、筛选全部下推到数据库：page / page_size 直接进 SQL 的 OFFSET/LIMIT，
    响应的 total 是同一套筛选条件下的总行数，前端据此渲染分页器再按页取数。
    不要把全量数据拉回前端再分页——语料一多，内存、带宽和首屏时间都会崩。

    响应里额外给出：
      - total_chars：与 total 同一口径的字符数合计（给汇总卡片用，避免前端只统计到当页）
      - documents：当前筛选范围内出现过的文档名，供前端渲染「来源文档」下拉

    文本段的 file_id 存在 cmetadata(JSONB) 里，用它关联 knowledge_files。
    不返回 embedding 向量本身——1024 维对前端没有用，且很占带宽。
    """
    user_collection_name = f"user_{current_user.id}"

    # 权限：公共库语料只有管理员能浏览，与 /file/list?scope=public 保持同一口径。
    # 普通用户无论传什么（含不带 scope 的缺省情形）都只能看自己私人库的语料 ——
    # 之前缺省是「私人 + 公共」，普通用户能靠它把公共库整库原文翻出来。
    # 注意这**不影响问答**：RAG 走 ChatAgent 内部的 store_for()，不经过这个接口，
    # 公共库语料照样会被检索到。
    is_admin = current_user.role == "admin"
    if not is_admin:
        if scope == "public":
            raise HTTPException(status_code=403, detail="仅管理员可查看公共知识库语料")
        scope = "private"

    # 私人集合。主键列是 uuid，不是 id。
    # 用户尚未上传过文件时该集合还不存在，此时返回空列表，不报 404。
    private_collection = (await db.execute(
        select(LangchainCollection)
        .where(LangchainCollection.name == user_collection_name)
    )).scalar_one_or_none()
    private_uuid = private_collection.uuid if private_collection is not None else None

    # 公共集合：knowledge_bases 里 owner_id 为空的都算公共库。
    # 非管理员上面已经把 scope 收成 private 了，这里不必再查。
    public_ids = []
    if is_admin:
        public_ids = (await db.execute(
            select(LangchainCollection.uuid)
            .join(KnowledgeBase, KnowledgeBase.collection_name == LangchainCollection.name)
            .where(KnowledgeBase.owner_id.is_(None), KnowledgeBase.is_deleted == False)
        )).scalars().all()

    # 按来源范围选集合：私人 / 公共 / 两者
    if scope == "private":
        collection_ids = [private_uuid] if private_uuid is not None else []
    elif scope == "public":
        collection_ids = list(public_ids)
    else:
        collection_ids = ([private_uuid] if private_uuid is not None else []) + list(public_ids)

    cmetadata = LangchainEmbedding.cmetadata
    file_id_text = cmetadata["file_id"].astext
    ingested_at_text = cmetadata["ingested_at"].astext
    seq_int = cast(cmetadata["seq"].astext, Integer)
    len_int = cast(cmetadata["len"].astext, Integer)
    join_file = KnowledgeFile.id == cast(file_id_text, Uuid)

    # 公共筛选条件：总数 / 汇总 / 文档名下拉 / 当页数据 共用这一套，保证四者口径一致
    base_filters = [LangchainEmbedding.collection_id.in_(collection_ids)]
    if segment_id:
        base_filters.append(LangchainEmbedding.id == segment_id)
    if content_type:
        # 文档类型在文件表上，文本段通过 file_id 关联过去
        base_filters.append(KnowledgeFile.content_type == content_type)
    if keyword and keyword.strip():
        pattern = f"%{_escape_like(keyword.strip())}%"
        base_filters.append(or_(
            LangchainEmbedding.document.ilike(pattern, escape="\\"),
            KnowledgeFile.original_name.ilike(pattern, escape="\\"),
            LangchainEmbedding.id.ilike(pattern, escape="\\"),
        ))

    # 文档名筛选只影响列表本身，不影响「来源文档」下拉的可选项（否则选中后下拉就只剩一项）
    filters = list(base_filters)
    if document:
        filters.append(KnowledgeFile.original_name == document)

    # 总数 + 字符数合计。SUM 与 COUNT 一次查询取回，口径一致
    total, total_chars = (await db.execute(
        select(func.count(LangchainEmbedding.id), func.coalesce(func.sum(len_int), 0))
        .join(KnowledgeFile, join_file, isouter=True)
        .where(*filters)
    )).one()

    # 来源文档下拉：当前范围内出现过的文档名
    doc_names = (await db.execute(
        select(KnowledgeFile.original_name)
        .select_from(LangchainEmbedding)
        .join(KnowledgeFile, join_file, isouter=True)
        .where(*base_filters)
        .distinct()
        .order_by(KnowledgeFile.original_name)
    )).scalars().all()

    # 当页数据。文本段左连接文件表：没有 file_id 或文件已删的段也保留，文件名返回空。
    # 默认排序：入库时间倒序 -> 文档名 -> 分段序号。
    # 同一份文件的分段共用同一个 ingested_at，所以时间倒序天然把一份文档聚在一起，
    # 最新入库的浮到最前；中间加文档名分组，避免不同文档的同序号交错。
    # NULLS LAST：缺元数据的历史分段排到最后，不会因为 DESC 而跑到最前面。
    rows = (await db.execute(
        select(LangchainEmbedding.id,
               LangchainEmbedding.document,
               LangchainEmbedding.cmetadata,
               LangchainEmbedding.collection_id,
               KnowledgeFile.original_name)
        .join(KnowledgeFile, join_file, isouter=True)
        .where(*filters)
        .order_by(
            ingested_at_text.desc().nullslast(),
            KnowledgeFile.original_name.asc().nullslast(),
            seq_int.asc().nullslast(),
            LangchainEmbedding.id.asc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).all()

    items = [
        {
            "id": row.id,
            "document": row.document,
            "file_name": row.original_name,
            "len": (row.cmetadata or {}).get("len"),
            "seq": (row.cmetadata or {}).get("seq"),
            "ingested_at": (row.cmetadata or {}).get("ingested_at"),
            # 标记来源，前端好区分私人段和公共段
            "scope": "private" if private_uuid is not None and row.collection_id == private_uuid else "public",
        }
        for row in rows
    ]

    return BaseResponse(code=200, message="获取成功", data={
        "total": total,
        "total_chars": int(total_chars or 0),
        "page": page,
        "page_size": page_size,
        "documents": [name for name in doc_names if name],
        "items": items,
    })

@router.get("/tags")
async def get_tags(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """统计数量。

    管理员看全库；普通用户只看自己私人知识库的。
    private_file_count / private_vector_count 是当前用户**本人私人库**的口径，
    两种角色都返回 —— 卡片上的「我的私人文档 / 私人语料」用它，不能复用管理员的
    vector_count（那是全库口径）。
    total_size / total_files 是整个存储桶的占用，只对管理员返回。
    """
    is_admin = current_user.role == "admin"

    # 先解析当前用户的私人库 —— 两种角色都要用：
    # 「我的私人文档 / 私人语料」卡片展示的是本人私人库，而管理员的 vector_count 是
    # 全库口径，拿它当私人库的数用会让同一屏出现两个互相矛盾的数字
    # （卡片标「全库 34 段」、私人库卡片也写 34 段，而私人库实际只有 8 段）。
    knowledge_base = (await db.execute(
        select(KnowledgeBase).where(KnowledgeBase.owner_id == current_user.id)
    )).scalar_one_or_none()

    private_vector_filter = []
    if knowledge_base is not None:
        collection = (await db.execute(
            select(LangchainCollection)
            .where(LangchainCollection.name == knowledge_base.collection_name)
        )).scalar_one_or_none()
        if collection is not None:
            private_vector_filter.append(LangchainEmbedding.collection_id == collection.uuid)

    # 软删的记录一律不计入统计。这一条必须放在这里，让下面所有 count_files 派生出的
    # 统计（stored / pending / rejected / public_* / file_count）都继承它 ——
    # /file/list 的第一条过滤条件就是 is_deleted == False，统计里不加的话两边口径就岔了：
    # 删掉的「已驳回」文件还会被计数，卡片显示 1 而列表里 0 条。
    if is_admin:
        # 管理员：文件统计不限知识库，向量统计是整库
        file_filter = [KnowledgeFile.is_deleted == False]
        vector_filter = []
    else:
        if knowledge_base is None:
            # 还没有私人知识库：统计一律归零，不报 404（否则页面加载会整体失败）
            return BaseResponse(code=200, message="获取成功", data={
                "stored_count": 0,
                "rejected_count": 0,
                "pending_count": 0,
                "vector_count": 0,
                "file_count": 0,
                "private_file_count": 0,
                "private_vector_count": 0,
            })
        file_filter = [KnowledgeFile.is_deleted == False,
                       KnowledgeFile.knowledge_base_id == knowledge_base.id]
        vector_filter = list(private_vector_filter)

    async def count_files(status: str | None = None, extra=None) -> int:
        """统计文件数。status 为 None 表示不限状态；extra 追加一条额外条件。"""
        where = list(file_filter)
        if status is not None:
            where.append(KnowledgeFile.status == status)
        if extra is not None:
            where.append(extra)
        return (await db.execute(
            select(func.count(KnowledgeFile.id)).where(*where)
        )).scalar_one()

    tags = {
        "stored_count": await count_files("stored"),
        "rejected_count": await count_files("rejected"),
        "pending_count": await count_files("pending"),
        "vector_count": (await db.execute(
            select(func.count(LangchainEmbedding.id)).where(*vector_filter)
        )).scalar_one(),
        # 私人库口径。与上面的 vector_count 不是一回事：管理员时一个是全库、一个是本人私人库
        "private_vector_count": (await db.execute(
            select(func.count(LangchainEmbedding.id)).where(*private_vector_filter)
        )).scalar_one(),
    }

    # 桶级占用无法按用户拆分，只给管理员；同时给出公共库口径的统计 ——
    # 文件列表改成服务端分页后，前端只拿得到当页数据，卡片上的数字必须由后端汇总。
    if is_admin:
        public_kb = await get_public_kb(db)
        public_keys = ("public_file_count", "public_stored_count",
                       "public_pending_count", "public_rejected_count", "public_pending_size")
        if public_kb is None:
            tags.update({key: 0 for key in public_keys})
        else:
            kb_filter = KnowledgeFile.knowledge_base_id == public_kb.id
            tags["public_file_count"] = await count_files(None, kb_filter)
            tags["public_stored_count"] = await count_files("stored", kb_filter)
            tags["public_pending_count"] = await count_files("pending", kb_filter)
            tags["public_rejected_count"] = await count_files("rejected", kb_filter)
            tags["public_pending_size"] = int((await db.execute(
                select(func.coalesce(func.sum(KnowledgeFile.size), 0))
                .where(KnowledgeFile.is_deleted == False,
                       kb_filter,
                       KnowledgeFile.status == "pending")
            )).scalar_one() or 0)

        total_size = 0
        total_files = 0
        for obj in client.list_objects(BUCKET, recursive=True):
            total_size += obj.size
            total_files += 1
        tags["total_size"] = total_size
        tags["total_files"] = total_files

        # 管理员也有自己的私人库。上面 file_filter 不限知识库，所以私人口径单独查一次。
        tags["private_file_count"] = (
            await count_files(None, KnowledgeFile.knowledge_base_id == knowledge_base.id)
            if knowledge_base is not None else 0
        )
    else:
        # 普通用户的私人文档总数，供「我的私人文档」卡片用（列表已分页，不能按当页算）
        tags["file_count"] = await count_files()
        tags["private_file_count"] = tags["file_count"]

    return BaseResponse(code=200, message="获取成功", data=tags)





