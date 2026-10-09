from typing import Any
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict
from model.base import Base
from sqlalchemy import Column, ForeignKey, Integer, Text, Uuid, String, DateTime, func, Boolean, CheckConstraint, \
    UniqueConstraint, Index, text


class ChatRequest(BaseModel):
    query: str
    # 是否启用知识库检索，由前端开关传入。
    rag_flag: bool = False
    # 使用哪个模型，取值见 GET /model/get；留空走默认模型。
    # 后端会校验，不在清单里返回 400（否则 ChatAgent 抛的 ValueError 会变成 500）。
    model_name: str | None = None
    # 会话 id。必须是 UUID —— 声明成 str 的话 "123456" 这类值能通过校验，
    session_id: UUID

class BaseResponse(BaseModel):
    code: int
    message: str
    data : Any | None = None

class ConversationDto(BaseModel):
    title: str | None = None

class ConversationVO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID
    title: str | None = None
    # 用 datetime 而不是 sqlalchemy 的 DateTime：后者是建表用的列类型，
    # Pydantic 无法为它生成 schema，会抛 PydanticSchemaGenerationError
    created_at: datetime
    updated_at: datetime

class ConversationMessageVO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    conversation_id: UUID
    role: str
    content: str
    sequence: int
    model: str | None = None
    status: str
    token_count: int | None = None
    parent_id: UUID | None = None
    created_at: datetime
    updated_at: datetime

# 声明基类见 model.base.Base。全项目只能有一个 Base，
# 否则外键跨模块引用会在映射时抛 NoReferencedTableError。


# 定义模型类（对应数据库中的一张表）
class Conversation(Base):
    """会话记录表。"""

    __tablename__ = "conversations"
    __table_args__ = (
        Index("idx_conversations_user_id", "user_id"),
        Index("idx_conversations_user_created", "user_id", text("created_at DESC")),
        {"comment": "会话记录表"},
    )

    id = Column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"), comment="主键")
    user_id = Column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, comment="所属用户")
    title = Column(String(255), nullable=True, server_default=text("''"), comment="会话标题")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(), comment="更新时间")
    is_deleted = Column(Boolean, nullable=False, server_default=text("false"), comment="软删除标记")


class ConversationMessage(Base):
    """会话消息表：一条会话（Conversation）下的一条消息。

    用户 id 不在这里冗余存储 —— 消息属于会话，会话属于用户，
    需要时通过 conversation_id join conversations 拿到。
    """

    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_messages_conv_seq"),
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="ck_messages_role"),
        CheckConstraint("status IN ('pending', 'done', 'error')", name="ck_messages_status"),
        # (conversation_id, sequence) 复合索引：同时覆盖"按会话查"和"按序号排序"
        Index("idx_messages_conv_seq", "conversation_id", "sequence"),
        {"comment": "会话消息表"},
    )

    id = Column(Uuid, primary_key=True, server_default=func.gen_random_uuid(), comment="主键")
    conversation_id = Column(Uuid, ForeignKey("conversations.id"), nullable=False, comment="所属会话 id")
    # user：用户输入；assistant：模型回复；system：系统提示词
    role = Column(String(16), nullable=False, comment="消息角色：user / assistant / system")
    content = Column(Text, nullable=False, comment="消息内容")
    # 会话内严格递增，保证消息顺序。created_at 在同一毫秒内可能重复，不能靠它排序
    sequence = Column(Integer, nullable=False, comment="会话内序号，从 1 开始递增")
    model = Column(String(64), nullable=True, comment="生成该回复所用的模型，用户消息为空")
    # pending：生成中；done：完成；error：生成失败或中断
    status = Column(String(16), nullable=False, server_default="done", comment="消息状态：pending / done / error")
    token_count = Column(Integer, nullable=True, comment="token 数，用于计费与上下文裁剪")
    # 重新生成时指向被重新生成的那条消息，形成分支
    parent_id = Column(Uuid, ForeignKey("conversation_messages.id"), nullable=True, comment="父消息 id，用于重新生成的分支")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(), comment="更新时间")
    is_deleted = Column(Boolean, nullable=False, server_default="false", comment="是否逻辑删除")