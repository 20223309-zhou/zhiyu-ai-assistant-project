import hashlib
import hmac
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, field_validator
from model.base import Base
from sqlalchemy import Column, String, Uuid, DateTime, Boolean, Integer, Text, func, Index, text

__all__ = ["Base", "User", "UserRegisterDto", "UserLoginDto", "UserVO", "LoginVO",
           "hash_password", "verify_password"]

# 固定盐：所有用户共用这一个值
_SALT = "salt"


def hash_password(raw: str) -> str:
    """把明文密码转成入库用的哈希值：md5(password.lower() + "salt")。
    """
    return hashlib.md5((raw.lower() + _SALT).encode("utf-8")).hexdigest()


def verify_password(raw: str, stored: str) -> bool:
    """校验明文密码是否与库里存的哈希匹配（登录时用）。"""
    # 用 compare_digest 而不是 ==：避免比较耗时随公共前缀长度变化而泄露信息
    return hmac.compare_digest(hash_password(raw), stored)


# 注册和登录必须用两个不同的 DTO —— 它们对 password 的语义要求正好相反：
#   注册：密码入库前必须被哈希
#   登录：密码必须保持明文，否则 verify_password 会算出"哈希的哈希"，
#         与库里的值永远不相等，正确密码也一律登录失败。
# 共用一个类就会踩这个坑，所以这里从类型上把它们分开。
class UserRegisterDto(BaseModel):
    """注册请求体：password 自动转成哈希后入库。"""
    username: str
    account: str
    password: str

    @field_validator("password")
    @classmethod
    def _hash_password(cls, raw: str) -> str:
        # 校验器是就地替换，所以 UserRegisterDto(password=...) 拿到的字段值
        # 已经是哈希后的结果，拿它去调 hash_password 会得到"哈希的哈希"。
        return hash_password(raw)


class UserLoginDto(BaseModel):
    """登录请求体：password 保持明文原样，交给 verify_password 校验。"""
    account: str
    password: str

class UserVO(BaseModel):
    """用户信息返回体：不包含密码字段。"""
    model_config = ConfigDict(from_attributes=True)
    id: UUID  # User.id 是 Uuid 列，存/取都是 uuid.UUID；写成 str 会类型对不上
    username: str
    account: str
    bio: str | None = None
    # 前端的「导入审核」等入口按角色显隐。不返回 role 的话前端拿不到管理员身份，
    # 只能一律当普通用户处理，管理员功能整个不可用。
    role: str = "user"


class LoginVO(BaseModel):
    """登录成功返回体：token 交给前端保存，后续请求放在 Authorization 头里。

    注意 token 只有签名、没有加密，payload 任何人都能读出来，
    所以里面只放用户 id 这类标识，不放手机号等敏感信息。
    """

    token: str
    token_type: str = "bearer"  # 前端拼成 "Bearer <token>" 放进 Authorization 头
    user: UserVO

# 声明基类见 model.base.Base。全项目只能有一个 Base，
# 否则外键跨模块引用会在映射时抛 NoReferencedTableError。


# 定义模型类（对应数据库中的一张表）
class User(Base):
    """用户表。"""

    __tablename__ = "users"
    __table_args__ = (
        # 登录账号只在"未删除"的用户中唯一：软删除后该账号可被重新注册
        Index("idx_users_account", "account", unique=True, postgresql_where=text("is_deleted = false")),
        Index("idx_users_account_deleted", "account", "is_deleted"),
        {"comment": "用户表"},
    )

    id = Column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"), comment="主键")
    username = Column(String(64), nullable=False, comment="账户名/昵称")
    account = Column(String(64), nullable=False, comment="登录账号，全局唯一")
    password = Column(String(255), nullable=False, comment="密码哈希值")
    bio = Column(Text, nullable=True, server_default=text("''"), comment="个人简介")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(), comment="更新时间")
    is_deleted = Column(Boolean, nullable=False, server_default=text("false"), comment="软删除标记")
    role = Column(String(16), nullable=False, server_default=text("'user'"), comment="用户角色：admin/user")
    # 登出 / 改密码时 +1，使该用户此前签发的所有 token 立即失效。
    # 见 auth.get_current_user 里对 payload["ver"] 的比对。
    token_version = Column(Integer, nullable=False, server_default=text("0"), comment="令牌版本号，登出或改密码时递增")