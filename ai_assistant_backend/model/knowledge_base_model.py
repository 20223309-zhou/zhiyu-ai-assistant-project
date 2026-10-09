"""知识库。一个知识库对应 langchain 里的一个向量集合。

owner_id 为空表示公共知识库，所有人可检索；
不为空表示私人知识库，只属于该用户。
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Uuid, func, text

from model.base import Base


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = {"comment": "知识库表"}

    id = Column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"), comment="主键")
    name = Column(String(128), nullable=False, comment="知识库名称")
    # 为空表示公共知识库
    owner_id = Column(Uuid, ForeignKey("users.id"), nullable=True, comment="拥有者 id，为空则为公共知识库")
    # 对应 langchain_pg_collection.name，用它定位向量集合
    collection_name = Column(String(128), nullable=False, unique=True, comment="对应的向量集合名称")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间")
    is_deleted = Column(Boolean, nullable=False, server_default=text("false"), comment="是否逻辑删除")
