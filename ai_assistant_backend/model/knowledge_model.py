"""知识库文件的元数据。

文件本体存在 MinIO，这张表只记录它的位置和业务状态，
两者通过 object_key 关联。
"""

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text, Uuid, func, text

from model.base import Base


class KnowledgeFile(Base):
    __tablename__ = "knowledge_files"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'stored', 'rejected', 'failed')",
            name="ck_knowledge_files_status",
        ),
        {"comment": "知识库文件表"},
    )

    id = Column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"), comment="主键")
    # 预留知识库分组。知识库表尚未建立，先不加外键，建表后再补
    knowledge_base_id = Column(Uuid, nullable=True, comment="所属知识库 id，知识库表建立前为空")
    uploader_id = Column(Uuid, ForeignKey("users.id"), nullable=False, comment="上传者 id")
    original_name = Column(String(255), nullable=False, comment="原始文件名")
    # MinIO 中的对象路径，数据库到文件本体的唯一关联。用 uuid 前缀避免重名覆盖
    object_key = Column(String(512), nullable=False, unique=True, comment="对象在 MinIO 中的路径")
    bucket = Column(String(64), nullable=False, server_default=text("'knowledge'"), comment="所在存储桶")
    content_type = Column(String(128), nullable=True, comment="MIME 类型")
    size = Column(BigInteger, nullable=False, comment="文件大小，单位字节")
    # 内容哈希，用于去重和识别同名但内容不同的文件
    file_hash = Column(String(64), nullable=True, comment="文件内容的 SHA-256")
    # pending 待审核 / approved 审核通过待解析 / stored 已入库 / rejected 已驳回 / failed 解析失败
    status = Column(String(16), nullable=False, server_default=text("'pending'"), comment="状态：pending/approved/stored/rejected/failed")
    reviewer_id = Column(Uuid, ForeignKey("users.id"), nullable=True, comment="审核人 id")
    review_comment = Column(Text, nullable=True, comment="审核意见，驳回时填写原因")
    reviewed_at = Column(DateTime(timezone=True), nullable=True, comment="审核时间")
    error_message = Column(Text, nullable=True, comment="解析失败原因")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(), comment="更新时间")
    is_deleted = Column(Boolean, nullable=False, server_default=text("false"), comment="是否逻辑删除")
