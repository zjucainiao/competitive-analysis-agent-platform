"""用户与鉴权 schema。

- ``User``：完整用户记录，**含 password_hash，仅服务端内部流转，绝不出 API**。
- ``UserPublic``：对外暴露的安全子集（无密码哈希）。

密码哈希用 bcrypt，签发的 JWT 里只放 user_id（sub），不放任何敏感信息。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class User(BaseModel):
    """完整用户记录。password_hash 绝不序列化进任何 API 响应。"""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    email: str  # 存储前已规范化为小写 trim
    password_hash: str
    display_name: str = ""
    created_at: datetime


class UserPublic(BaseModel):
    """对外暴露的用户信息（无密码哈希）。"""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    email: str
    display_name: str = ""
    created_at: datetime

    @classmethod
    def of(cls, u: User) -> "UserPublic":
        return cls(
            user_id=u.user_id,
            email=u.email,
            display_name=u.display_name,
            created_at=u.created_at,
        )
