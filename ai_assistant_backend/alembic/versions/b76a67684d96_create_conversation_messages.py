"""create conversation_messages

Revision ID: b76a67684d96
Revises: cda2b1b4b4d1
Create Date: 2026-09-29 14:15:35.623348

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b76a67684d96'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建会话消息表。

    手写而非 autogenerate：当前模型与已有库的差异很大（两套 Base、类型不一致、
    库里有模型没声明的索引），autogenerate 会把无关的变更一并生成出来。
    这张表的结构以 model.agent_model.ConversationMessage 为准。
    """
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False, comment="主键"),
        sa.Column("conversation_id", sa.Uuid(), nullable=False, comment="所属会话 id"),
        sa.Column("role", sa.String(length=16), nullable=False, comment="消息角色：user / assistant / system"),
        sa.Column("content", sa.Text(), nullable=False, comment="消息内容"),
        sa.Column("sequence", sa.Integer(), nullable=False, comment="会话内序号，从 1 开始递增"),
        sa.Column("model", sa.String(length=64), nullable=True, comment="生成该回复所用的模型，用户消息为空"),
        sa.Column("status", sa.String(length=16), server_default="done", nullable=False, comment="消息状态：pending / done / error"),
        sa.Column("token_count", sa.Integer(), nullable=True, comment="token 数，用于计费与上下文裁剪"),
        sa.Column("parent_id", sa.Uuid(), nullable=True, comment="父消息 id，用于重新生成的分支"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False, comment="更新时间"),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False, comment="是否逻辑删除"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["parent_id"], ["conversation_messages.id"]),
        sa.UniqueConstraint("conversation_id", "sequence", name="uq_messages_conv_seq"),
        sa.CheckConstraint("role IN ('user', 'assistant', 'system')", name="ck_messages_role"),
        sa.CheckConstraint("status IN ('pending', 'done', 'error')", name="ck_messages_status"),
        comment="会话消息表",
    )
    # (conversation_id, sequence) 复合索引：同时覆盖"按会话查"和"按序号排序"，
    # 不必再单独建 conversation_id 索引
    op.create_index(
        "idx_messages_conv_seq",
        "conversation_messages",
        ["conversation_id", "sequence"],
    )


def downgrade() -> None:
    """删除会话消息表。"""
    op.drop_index("idx_messages_conv_seq", table_name="conversation_messages")
    op.drop_table("conversation_messages")
