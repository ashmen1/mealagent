from __future__ import annotations

from sqlalchemy.orm import Session

from .models import UserProfile


class ProfileRepositoryError(RuntimeError):
    """读取用户健康档案失败。"""


def load_user_profile(
    session: Session,
    profile_id: int,
) -> UserProfile | None:
    """按主键读取一条用户健康档案。"""

    if not isinstance(session, Session):
        raise ProfileRepositoryError("数据库 Session 无效")

    try:
        return session.get(UserProfile, profile_id)
    except Exception as exc:
        raise ProfileRepositoryError("查询用户健康档案失败") from exc


__all__ = ["ProfileRepositoryError", "load_user_profile"]
