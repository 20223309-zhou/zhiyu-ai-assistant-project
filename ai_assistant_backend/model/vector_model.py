"""langchain PGVector 用的两张表的模型映射。

表本身由 langchain_postgres 在首次使用时自动创建，这里的模型只是
映射已有的表，供业务代码查询使用，不负责建表。

注意列类型必须和库里完全一致（cmetadata 一列是 json、另一列是 jsonb，
embedding 是 vector(1024)），否则 alembic autogenerate 会误判为类型变更。
"""

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, ForeignKey, String, Uuid
from sqlalchemy.dialects.postgresql import JSON, JSONB

from model.base import Base


class LangchainCollection(Base):
    """向量集合表。一个 collection 对应一类文档，如 my_docs。"""

    __tablename__ = "langchain_pg_collection"
    __table_args__ = {"comment": "向量集合表"}

    uuid = Column(Uuid, primary_key=True, comment="主键")
    name = Column(String, nullable=False, unique=True, comment="集合名称")
    cmetadata = Column(JSON, nullable=True, comment="集合级元数据")


class LangchainEmbedding(Base):
    """向量表。每个文本段一行，embedding 为 1024 维向量。"""

    __tablename__ = "langchain_pg_embedding"
    __table_args__ = {"comment": "向量嵌入表"}

    id = Column(String, primary_key=True, comment="主键")
    collection_id = Column(
        Uuid,
        ForeignKey("langchain_pg_collection.uuid", ondelete="CASCADE"),
        nullable=True,
        comment="所属集合 id",
    )
    embedding = Column(Vector(1024), nullable=True, comment="1024 维向量")
    document = Column(String, nullable=True, comment="文本段内容")
    cmetadata = Column(JSONB, nullable=True, comment="文本段元数据")
