from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


class DialogueMenuStateMigrationError(RuntimeError):
    """对话菜单状态列迁移失败。"""


def migrate_dialogue_menu_state(engine: Engine) -> None:
    """幂等补齐 Spec_15 所需 JSON 状态列。"""

    if not isinstance(engine, Engine):
        raise DialogueMenuStateMigrationError("数据库 Engine 无效")
    try:
        with engine.begin() as connection:
            inspector = inspect(connection)
            if inspector.has_table("dialogue_sessions"):
                existing = {
                    column["name"]
                    for column in inspector.get_columns("dialogue_sessions")
                }
                for name in (
                    "last_menu",
                    "excluded_recipe_names",
                    "pending_menu_change",
                ):
                    if name not in existing:
                        connection.execute(
                            text(
                                f"ALTER TABLE dialogue_sessions "
                                f"ADD COLUMN {name} JSON"
                            )
                        )
            if inspector.has_table("dialogue_turns"):
                turn_columns = {
                    column["name"]
                    for column in inspector.get_columns("dialogue_turns")
                }
                if "menu_change" not in turn_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE dialogue_turns "
                            "ADD COLUMN menu_change JSON"
                        )
                    )
    except Exception as exc:
        raise DialogueMenuStateMigrationError(
            "迁移对话菜单状态列失败"
        ) from exc


__all__ = [
    "DialogueMenuStateMigrationError",
    "migrate_dialogue_menu_state",
]
