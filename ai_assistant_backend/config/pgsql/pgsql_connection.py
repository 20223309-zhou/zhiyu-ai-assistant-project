from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

__all__ = ["get_db","connection","lifespan","FastAPI","AsyncSession"]

# 创建异步引擎
ASYNC_DATABASE_URL = "postgresql+asyncpg://postgres:1234@192.168.100.5:5432/ai"
connection = create_async_engine(
    ASYNC_DATABASE_URL,
    echo=True,  # 打印SQL语句
    pool_size=10,  # 连接池活跃的连接数
    max_overflow=20  # 最大溢出连接数
)

# 定义 lifespan 上下文管理器
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 确保存在一个公共知识库：langchain 侧登记集合，本地表记录归属。
    # 已存在则不动。放在启动时，保证服务起来就有公共库可用。
    from sqlalchemy import select

    from config.pgsql.pgvector_client import create_collection
    from model.knowledge_base_model import KnowledgeBase

    PUBLIC_COLLECTION = "public"
    async with AsyncSession(connection) as session:
        existing = (await session.execute(
            select(KnowledgeBase).where(KnowledgeBase.collection_name == PUBLIC_COLLECTION)
        )).scalar_one_or_none()
        if existing is None:
            create_collection(PUBLIC_COLLECTION)
            session.add(KnowledgeBase(
                name="公共知识库",
                owner_id=None,
                collection_name=PUBLIC_COLLECTION,
            ))
            await session.commit()
    yield  # 应用运行中
    await connection.dispose()  # 优雅关闭连接池


# 创建依赖项：每次请求自动提供 AsyncSession
async def get_db() -> AsyncSession:
    # expire_on_commit=False：commit() 之后对象属性不再过期。
    # 默认的 True 会让「commit 后再读一次属性」变成一次懒加载，而在异步 Session 里
    # 同步懒加载会直接抛 sqlalchemy.exc.MissingGreenlet
    # （greenlet_spawn has not been called），是异步 SQLAlchemy 最常见的坑之一。
    async with AsyncSession(connection, expire_on_commit=False) as session:
        yield session