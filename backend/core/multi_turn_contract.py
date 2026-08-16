from __future__ import annotations

import copy
from typing import Any, Final

from backend.core.dialogue_constraint_contract import (
    CONSTRAINT_OUTPUT_SCHEMA,
    TOP_LEVEL_FIELDS,
)


SESSION_STATUSES: Final = (
    "in_progress",
    "needs_confirmation",
    "ready_for_planning",
)

CHANGE_ACTIONS: Final = ("add", "replace", "remove")

CHANGEABLE_TOP_FIELDS: Final = (
    "meal_periods",
    "diner_count",
    "max_total_time_minutes",
    "available_ingredients",
)

SCALAR_FIELDS: Final = ("diner_count", "max_total_time_minutes")

CHANGE_ACTION_FIELDS: Final = ("field", "dish_index", "action", "evidence")

MULTI_TURN_TOP_LEVEL_FIELDS: Final = TOP_LEVEL_FIELDS + ("change_actions",)

MISSING_REQUIREMENTS: Final = ("人数", "明确菜品类型")


class MultiTurnConstraintError(Exception):
    """多轮约束会话的可预期接口错误。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


CHANGE_ACTION_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": list(CHANGE_ACTION_FIELDS),
    "properties": {
        "field": {
            "anyOf": [
                {"type": "string", "enum": list(CHANGEABLE_TOP_FIELDS)},
                {"type": "null"},
            ]
        },
        "dish_index": {
            "anyOf": [
                {"type": "integer", "minimum": 0},
                {"type": "null"},
            ]
        },
        "action": {
            "type": "string",
            "enum": list(CHANGE_ACTIONS),
        },
        "evidence": {"type": "string"},
    },
}

MULTI_TURN_OUTPUT_SCHEMA: Final[dict[str, Any]] = copy.deepcopy(
    CONSTRAINT_OUTPUT_SCHEMA
)
MULTI_TURN_OUTPUT_SCHEMA["title"] = "MultiTurnDialogueConstraints"
MULTI_TURN_OUTPUT_SCHEMA["description"] = (
    "多轮中文对话中提取出的完整更新约束与变更声明。"
)
MULTI_TURN_OUTPUT_SCHEMA["required"] = list(MULTI_TURN_TOP_LEVEL_FIELDS)
MULTI_TURN_OUTPUT_SCHEMA["properties"]["change_actions"] = {
    "type": "array",
    "items": CHANGE_ACTION_SCHEMA,
}


__all__ = [
    "CHANGEABLE_TOP_FIELDS",
    "CHANGE_ACTIONS",
    "CHANGE_ACTION_FIELDS",
    "CHANGE_ACTION_SCHEMA",
    "MISSING_REQUIREMENTS",
    "MULTI_TURN_OUTPUT_SCHEMA",
    "MULTI_TURN_TOP_LEVEL_FIELDS",
    "MultiTurnConstraintError",
    "SCALAR_FIELDS",
    "SESSION_STATUSES",
]
