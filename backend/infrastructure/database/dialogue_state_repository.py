from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import DialogueSession, DialogueTurn


class DialogueStateRepositoryError(RuntimeError):
    """读写会话状态失败。"""


def insert_dialogue_session(
    session: Session,
    profile_id: int,
) -> int:
    """创建 in_progress 会话行并返回其id;由调用方提交事务。"""

    if not isinstance(session, Session):
        raise DialogueStateRepositoryError("数据库 Session 无效")

    try:
        row = DialogueSession(
            profile_id=profile_id,
            status="in_progress",
            merged_constraints=None,
        )
        session.add(row)
        session.flush()
        return row.id
    except Exception as exc:
        raise DialogueStateRepositoryError("创建会话失败") from exc


def load_dialogue_session(
    session: Session,
    session_id: int,
    for_update: bool = False,
) -> DialogueSession | None:
    """按主键读取会话行;for_update 为真时加行锁。"""

    if not isinstance(session, Session):
        raise DialogueStateRepositoryError("数据库 Session 无效")

    try:
        statement = select(DialogueSession).where(
            DialogueSession.id == session_id
        )
        if for_update:
            statement = statement.with_for_update()
        return session.execute(statement).scalar_one_or_none()
    except Exception as exc:
        raise DialogueStateRepositoryError("查询会话失败") from exc


def next_turn_number(session: Session, session_id: int) -> int:
    """计算会话的下一个轮次序号(当前最大值加一)。"""

    if not isinstance(session, Session):
        raise DialogueStateRepositoryError("数据库 Session 无效")

    try:
        max_number = session.execute(
            select(func.max(DialogueTurn.turn_number)).where(
                DialogueTurn.session_id == session_id
            )
        ).scalar_one()
    except Exception as exc:
        raise DialogueStateRepositoryError("计算轮次序号失败") from exc
    return (max_number or 0) + 1


def insert_dialogue_turn(
    session: Session,
    session_id: int,
    turn_number: int,
    user_message: str,
) -> None:
    """写入一条轮次记录;由调用方提交事务。"""

    if not isinstance(session, Session):
        raise DialogueStateRepositoryError("数据库 Session 无效")

    try:
        session.add(
            DialogueTurn(
                session_id=session_id,
                turn_number=turn_number,
                user_message=user_message,
            )
        )
    except Exception as exc:
        raise DialogueStateRepositoryError("写入轮次失败") from exc


def update_dialogue_session_state(
    session: Session,
    session_row: DialogueSession,
    merged_constraints: dict[str, Any],
    status: str,
) -> None:
    """更新会话行的合并约束与状态;由调用方提交事务。"""

    if not isinstance(session, Session):
        raise DialogueStateRepositoryError("数据库 Session 无效")

    session_row.merged_constraints = merged_constraints
    session_row.status = status


__all__ = [
    "DialogueStateRepositoryError",
    "insert_dialogue_session",
    "insert_dialogue_turn",
    "load_dialogue_session",
    "next_turn_number",
    "update_dialogue_session_state",
]
