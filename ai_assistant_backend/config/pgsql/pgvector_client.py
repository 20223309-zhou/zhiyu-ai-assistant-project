from langchain.embeddings import init_embeddings
from langchain_postgres import PGVector

# 连接字符串
connection = "postgresql+psycopg://postgres:1234@192.168.100.5:5432/ai?sslmode=disable"

# 初始化嵌入模型
embedding = init_embeddings(
    model="openai:qwen3-embedding:0.6b",
    api_key="ollama",
    base_url="http://localhost:11434/v1",
    check_embedding_ctx_length=False
)

def store_for(collection_name: str) -> PGVector:
    """返回指向指定集合的向量库句柄。

    复用已有的 embedding 模型和数据库连接，不重新加载模型、不新建连接，
    所以构造开销很小，可以按集合名随时创建。
    """
    return PGVector(
        embeddings=embedding,
        collection_name=collection_name,
        connection=connection,
        embedding_length=1024,
        use_jsonb=True,
    )


def create_collection(collection_name: str) -> None:
    """在 langchain_pg_collection 中登记一个集合（已存在则不动）。

    同步阻塞，调用方在异步上下文里要放进线程池。
    """
    store_for(collection_name).create_collection()