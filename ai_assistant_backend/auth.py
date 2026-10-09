"""鉴权模块：签发 / 校验 JWT，并提供 get_current_user 依赖。

用法：需要登录的接口挂上 `current_user: User = Depends(get_current_user)`，
校验在进入函数体之前完成，失败直接 401 —— 函数体内可以认为"一定有人登录着"。
"""

import os
from datetime import datetime, timedelta, timezone
from uuid import UUID
from collections.abc import Callable
import jwt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model.user_model import User
from config.pgsql.pgsql_connection import get_db

load_dotenv()

# 密钥只从 .env 读。硬编码进代码 = 拿到源码的人就能签发任意用户的 token。
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("缺少 JWT_SECRET_KEY，请在 .env 中配置后重启")

ALGORITHM = "HS256"
EXPIRE_MINUTES = 60 * 24  # 24 小时

# auto_error=False：不在这里直接抛错，改由 get_current_user 统一处理，
# 这样 401 的文案和响应头可控（默认行为是 403，语义不对）。
bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    # 按 RFC 6750，401 应带上 WWW-Authenticate 头
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def create_access_token(user_id, token_version: int) -> str:
    """签发 token。

    sub 必须是字符串 —— JWT 规范如此，PyJWT 对非字符串会报错。
    ver 写入签发时的 token_version，校验时与库里的当前值比对，
    对不上就说明用户在这之后登出过（或改过密码），token 作废。
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "ver": token_version,
        "iat": now,                                   # 签发时间
        "exp": now + timedelta(minutes=EXPIRE_MINUTES),  # 过期时间
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    """从 Authorization: Bearer <token> 解出当前用户；任何一环失败都返回 401。

    这个函数被 router 级和参数级同时依赖时只会执行一次 ——
    FastAPI 默认对同一依赖做请求内缓存（use_cache=True）。
    """
    if credentials is None:
        raise _unauthorized("未登录")

    try:
        # algorithms 必须显式指定，否则存在算法混淆攻击的风险
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        # 注意：ExpiredSignatureError 是 InvalidTokenError 的子类，
        # 必须排在前面捕获，否则永远走不到这个分支。
        raise _unauthorized("登录已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise _unauthorized("无效的登录凭证")

    subject = payload.get("sub")
    try:
        user_id = UUID(subject)
    except (TypeError, ValueError):
        # sub 不是合法 UUID —— 正常签发的 token 不会出现，
        # 但这里挡一下，避免变成 500。
        raise _unauthorized("无效的登录凭证")

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or user.is_deleted:
        raise _unauthorized("用户不存在或已注销")

    # 版本号对不上：用户在 token 签发之后登出过，或改过密码。
    if payload.get("ver") != user.token_version:
        raise _unauthorized("登录已失效，请重新登录")
    return user

def require_roles(*allowed: str) -> Callable:
    """返回一个依赖：当前用户的 role 必须在 allowed 里，否则 403。
    自带登录校验 —— 它依赖 get_current_user，未登录会先被 401 拦下。
    """
    async def _check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed:
            raise HTTPException(status_code=403, detail="权限不足")
        return current_user
    return _check


require_admin = require_roles("admin")
