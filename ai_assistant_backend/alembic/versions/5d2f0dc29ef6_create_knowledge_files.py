"""create knowledge_files

Revision ID: 5d2f0dc29ef6
Revises: b76a67684d96
Create Date: 2026-09-29 19:47:21.575269

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5d2f0dc29ef6'
down_revision: Union[str, Sequence[str], None] = 'b76a67684d96'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建知识库文件表。

    手写而非 autogenerate：数据库里还有 langgraph 的 checkpoint_* 表，
    它们不在 SQLAlchemy 模型中，autogenerate 会把它们误判为"应删除的表"。
    这张表的结构以 model.knowledge_model.KnowledgeFile 为准。
    """
    op.create_table(
        "knowledge_files",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False, comment="主键"),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=True, comment="所属知识库 id，知识库表建立前为空"),
        sa.Column("uploader_id", sa.Uuid(), nullable=False, comment="上传者 id"),
        sa.Column("original_name", sa.String(length=255), nullable=False, comment="原始文件名"),
        sa.Column("object_key", sa.String(length=512), nullable=False, comment="对象在 MinIO 中的路径"),
        sa.Column("bucket", sa.String(length=64), server_default=sa.text("'knowledge'"), nullable=False, comment="所在存储桶"),
        sa.Column("content_type", sa.String(length=128), nullable=True, comment="MIME 类型"),
        sa.Column("size", sa.BigInteger(), nullable=False, comment="文件大小，单位字节"),
        sa.Column("file_hash", sa.String(length=64), nullable=True, comment="文件内容的 SHA-256"),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False, comment="状态：pending/approved/stored/rejected/failed"),
        sa.Column("reviewer_id", sa.Uuid(), nullable=True, comment="审核人 id"),
        sa.Column("review_comment", sa.Text(), nullable=True, comment="审核意见，驳回时填写原因"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True, comment="审核时间"),
        sa.Column("error_message", sa.Text(), nullable=True, comment="解析失败原因"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False, comment="更新时间"),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False, comment="是否逻辑删除"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["uploader_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"]),
        sa.UniqueConstraint("object_key"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'stored', 'rejected', 'failed')",
            name="ck_knowledge_files_status",
        ),
        comment="知识库文件表",
    )


def downgrade() -> None:
    """删除知识库文件表。"""
    op.drop_table("knowledge_files")
