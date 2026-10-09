import asyncio
import os
import threading

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from langchain_core.prompts import ChatPromptTemplate
from config.pgsql.pgvector_client import store_for
# 加载环境变量
load_dotenv()

mimo_model_name = os.getenv("MIMO_MODEL_NAME")
mimo_api_key = os.getenv("MIMO_API_KEY")
mimo_base_url = os.getenv("MIMO_BASE_URL")

deepseek_model_name = os.getenv("DEEPSEEK_MODEL_NAME")
deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")
deepseek_base_url = os.getenv("DEEPSEEK_BASE_URL")
model_list = {
    mimo_model_name: {
        "api_key": mimo_api_key,
        "base_url": mimo_base_url
    },
    deepseek_model_name: {
        "api_key": deepseek_api_key,
        "base_url": deepseek_base_url
    }
}
DB_URL = os.getenv("DB_URL")

# 连接池在进程生命周期内存活。
# 不要用 PostgresSaver.from_conn_string() 的 with 块 —— 它退出时会 close 连接，
# 之后 saver 还用这条连接就会报 "the connection is closed"。
pool = ConnectionPool(
    conninfo=DB_URL,
    min_size=1,
    max_size=10,
    # 这三项必须和 from_conn_string 内部保持一致
    kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
)
checkpointer = PostgresSaver(pool)
# 首次使用前必须建表，否则读写 checkpoint 时会报 relation "checkpoints" does not exist。
# setup() 内部是 CREATE TABLE IF NOT EXISTS，重复调用是安全的。
checkpointer.setup()

RAG_SYSTEM_PROMPT = """你是一个严谨的知识库问答助手，只依据给定的【已知信息】作答。

1. 只使用【已知信息】的内容作答，不补充外部知识，不推测、不编造。
2. 每个结论后用 [序号] 标注来源，例如：Java 先编译成字节码 [2]。
3. 若【已知信息】不足以回答，只回复：根据已知信息无法回答该问题。
4. 若只覆盖了问题的一部分，先答能确定的部分，再说明哪部分资料中没有。
5. 使用与提问相同的语言，用 Markdown 组织答案。"""

# 用户关掉知识库检索时用这套。
# 必须和上面那套分开：没有【已知信息】的时候若还沿用「只依据已知信息作答 + 不足以回答就回复
# 无法回答」，模型会按规则 3 对每个问题都回「根据已知信息无法回答该问题」，通用对话直接报废。
CHAT_SYSTEM_PROMPT = """你是一个耐心、亲切的 AI 助手，善于倾听并理解用户的需求。
回答准确、条理清晰，用 Markdown 组织答案，语言与用户提问保持一致。
不确定的信息要如实说明，不要编造。"""

prompt_template = ChatPromptTemplate.from_messages([
    ("human", "【已知信息】\n{context}\n\n【用户问题】\n{question}"),
])

# 检索为空时单独走这套，避免把空 context 当"已知信息"
no_context_template = ChatPromptTemplate.from_messages([
    ("human", "本轮没有检索到相关资料。\n\n【用户问题】\n{question}"),
])
# model只加载一次，避免重复加载模型
# stream_usage=True：流式调用时让接口在最后一个 chunk 带回用量统计。
# 不开的话 response_metadata / usage_metadata 里没有 token 数，落库时只能存 None。
mimo_model = init_chat_model(model=mimo_model_name,
                            api_key=mimo_api_key,
                            model_provider="openai",
                            base_url=mimo_base_url,
                            temperature=0.7,
                            stream_usage=True)

deepseek_model = init_chat_model(model=deepseek_model_name,
                            api_key=deepseek_api_key,
                            model_provider="openai",
                            base_url=deepseek_base_url,
                            temperature=0.7,
                            stream_usage=True)

# 可用模型清单：模型对象、展示名、默认项都从这里派生，
# 不要在别处再写一份名单（前端下拉走 /model/get，同样不硬编码）
_MODEL_SPECS = (
    (mimo_model_name, mimo_model, "MiMo v2.6 Flash"),
    (deepseek_model_name, deepseek_model, "DeepSeek Flash"),
)

DEFAULT_MODEL_NAME = mimo_model_name


def list_models() -> list[dict]:
    """给前端渲染模型下拉用。"""
    return [
        {"name": name, "label": label, "is_default": name == DEFAULT_MODEL_NAME}
        for name, _model, label in _MODEL_SPECS
    ]


def is_supported_model(model_name: str | None) -> bool:
    return bool(model_name) and any(name == model_name for name, _m, _l in _MODEL_SPECS)


def _build_agents() -> dict:
    """每个模型建两份 agent：知识库问答版 + 通用对话版。
    """
    agents = {}
    for model_name, model, _label in _MODEL_SPECS:
        agents[(model_name, True)] = create_agent(
            model=model, system_prompt=RAG_SYSTEM_PROMPT, checkpointer=checkpointer, tools=[])
        agents[(model_name, False)] = create_agent(
            model=model, system_prompt=CHAT_SYSTEM_PROMPT, checkpointer=checkpointer, tools=[])
    return agents


_agents = _build_agents()


def _chunk_to_text(content) -> str:
    """把流式 chunk 的 content 归一化成纯文本。

    content 可能是 str，也可能是 [{"type": "text", "text": ...}] 这种分块列表
    （OpenAI 兼容接口在思考链 / 多模态场景下会返回列表），不能直接当字符串拼。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text") or "")
        return "".join(parts)
    return ""

# 相关性阈值。score 是余弦相似度（PGVector 默认 COSINE）。
# 实测：相关问句 0.67~0.74，不相关问句最高 0.31，间隔带很宽，0.55 落在安全区。
RELEVANCE_THRESHOLD = 0.55


def _search_one(store, user_input: str) -> list:
    """单路检索。失败时返回空列表而不是往上抛。

    用户还没有私人知识库时对应集合不存在，检索会报错；
    这时应当降级成「没有私人语料」，不能让它把整条 RAG 链路打断。
    """
    try:
        return store.similarity_search_with_relevance_scores(user_input, k=3)
    except Exception:
        return []


def get_retrieved_context(user_input: str, collection_name: str):
    # 1、向量检索，获取与用户输入最相关的文档
    private_results = _search_one(store_for(collection_name), user_input) if collection_name else []
    public_results = _search_one(store_for("public"), user_input)

    # 2、过滤低分 + 去重，再拼成上下文。
    # 去重是必要的：同一份文档可能既在私人库又在公共库，两路都会命中同一段。
    blocks = []
    seen = set()
    for scope, results in (("私人知识库", private_results), ("公共知识库", public_results)):
        for doc, score in results:
            if score < RELEVANCE_THRESHOLD:
                continue
            key = doc.page_content.strip()
            if key in seen:
                continue
            seen.add(key)
            # source 在入库时已被覆盖成原始文件名（见 knowledge.py::_ingest）
            source = doc.metadata.get("source") or "未知文档"
            blocks.append(f"来源文件：{scope}/{source}\n检索内容：{doc.page_content}")

    if not blocks:
        return no_context_template.format_messages(question=user_input)
    # 3、将检索内容和用户输入传入 prompt 模板，生成
    # 注意：两路结果要合到一个列表后再 join，否则私人段和公共段之间会缺分隔符、
    # 直接粘成「…检索内容：xxx来源文件：公共知识库/…」
    return prompt_template.format_messages(context="\n---\n".join(blocks), question=user_input)
# 面向对象封装聊天代理类
class ChatAgent():

    # 获取agent。rag_flag 决定用哪一份：知识库问答版 or 通用对话版
    @staticmethod
    def get_agent(model_name: str, rag_flag: bool = False):
        agent = _agents.get((model_name, bool(rag_flag)))
        if agent is None:
            raise ValueError(f"不支持的模型: {model_name}，可选: {sorted({k[0] for k in _agents})}")
        return agent

    def __init__(self, model_name=mimo_model_name, chat_id: str = "1"):
        # agent 不在这里定：知识库开关是按次请求传进来的，同一会话里可能来回切
        self.model_name = model_name
        self.config = {"configurable": {"thread_id": chat_id}}

    def chat(self, user_input: str, rag_flag: bool = False, collection_name: str = None):
        """一次性生成（非流式）。同步阻塞，调用方要放进线程池，否则会卡住事件循环。"""
        agent = ChatAgent.get_agent(self.model_name, rag_flag)
        if rag_flag:
            prompt = get_retrieved_context(user_input, collection_name)
        else:
            prompt = HumanMessage(content=user_input)
        return agent.invoke({"messages": prompt}, config=self.config)

    async def chat_stream(self, user_input: str, rag_flag: bool = False,
                          collection_name: str = None):
        """流式生成，逐段产出事件字典：

            {"type": "token", "text": "增量文本"}
            {"type": "usage", "usage": {...}}      # 末尾一次，拿不到就是空 dict
            {"type": "error", "message": "..."}    # 生成失败时

        **为什么用线程桥接而不是 agent.astream()**：
        PostgresSaver 是同步实现的 checkpointer，它的异步方法
        （aget_tuple / aput / adelete_thread）全是基类里直接 raise NotImplementedError，
        所以 astream() 一进去读 checkpoint 就炸。改用同步的 agent.stream() 丢进线程，
        再把增量经 asyncio.Queue 搬回事件循环 —— 好处是不用再引一套 AsyncPostgresSaver
        连接池，而且流式与非流式共用同一个 checkpointer，会话历史天然连续。

        检索是同步阻塞调用，先用 to_thread 丢出去；直接在协程里跑会卡住事件循环，
        首 token 延迟会被检索时间叠加得更明显。
        """
        agent = ChatAgent.get_agent(self.model_name, rag_flag)
        if rag_flag:
            prompt = await asyncio.to_thread(get_retrieved_context, user_input, collection_name)
        else:
            prompt = HumanMessage(content=user_input)

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        done_marker = object()

        def worker():
            usage = {}
            try:
                for chunk, _metadata in agent.stream(
                    {"messages": prompt}, config=self.config, stream_mode="messages"
                ):
                    text = _chunk_to_text(getattr(chunk, "content", None))
                    if text:
                        # 跨线程写队列必须走 call_soon_threadsafe，否则不是线程安全的
                        loop.call_soon_threadsafe(
                            queue.put_nowait, {"type": "token", "text": text})
                    # 开了 stream_usage 后，用量出现在最后一个 chunk 上
                    chunk_usage = getattr(chunk, "usage_metadata", None)
                    if chunk_usage:
                        usage = dict(chunk_usage)
                loop.call_soon_threadsafe(queue.put_nowait, {"type": "usage", "usage": usage})
            except Exception as exc:
                loop.call_soon_threadsafe(
                    queue.put_nowait, {"type": "error", "message": str(exc)})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, done_marker)

        thread = threading.Thread(target=worker, name="chat-stream", daemon=True)
        thread.start()
        try:
            while True:
                item = await queue.get()
                if item is done_marker:
                    break
                yield item
        finally:
            # 客户端断开时生成器会被 close()，等线程收尾一下。
            # 线程是 daemon：就算模型调用还没返回，也不会挡住进程退出。
            await asyncio.to_thread(thread.join, 5)