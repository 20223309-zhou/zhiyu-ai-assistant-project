import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 注册模型的 metadata，autogenerate 靠它对比"模型"和"数据库"的差异来生成迁移。
# 所有模型共用 model.base.Base 这一个声明基类，import 任一模型模块都会把表注册进来。
import model.user_model  # noqa: F401  确保模型被注册
import model.agent_model  # noqa: F401
import model.knowledge_model  # noqa: F401
import model.knowledge_base_model  # noqa: F401
import model.vector_model  # noqa: F401
from model.base import Base

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # 这些表不由 SQLAlchemy 模型管理，而是由第三方库自行创建：
    #   checkpoint*  -> langgraph 的 PostgresSaver（会话记忆）
    #   langchain_*  -> langchain_postgres 的 PGVector（向量库）
    # 不过滤的话，autogenerate 会把"数据库有、模型没有"误判成"该删除"，
    # 生成 drop_table。按表名前缀排除。
    EXCLUDED_PREFIXES = ("checkpoint", "langchain_")

    def include_object(obj, name, type_, reflected, compare_to):
        if type_ == "table" and name.startswith(EXCLUDED_PREFIXES):
            return False
        return True

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def _database_url() -> str:
    """从 .env 读数据库地址，并转换成异步驱动。

    不写进 alembic.ini 是为了避免把密码提交进仓库（alembic.ini 会被 git 跟踪）。
    .env 里的 DB_URL 用的是同步驱动 postgresql://，alembic 这里跑的是异步引擎，
    所以替换成 postgresql+asyncpg://。
    """
    import os

    from dotenv import load_dotenv

    load_dotenv()
    url = os.getenv("DB_URL")
    if not url:
        raise RuntimeError("缺少 DB_URL，请在 .env 中配置")
    return url.replace("postgresql://", "postgresql+asyncpg://", 1).split("?")[0]


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    config.set_main_option("sqlalchemy.url", _database_url())

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
