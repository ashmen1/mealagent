from __future__ import annotations

from typing import Any, Final


MERGED_CONSTRAINT_FIELDS: Final = (
    "dialogue_id",
    "meal_periods",
    "diner_count",
    "total_dish_count",
    "max_total_time_minutes",
    "max_difficulty",
    "available_ingredients",
    "dishes",
    "evidence",
)
TOP_LEVEL_FIELDS: Final = MERGED_CONSTRAINT_FIELDS + ("change_actions",)
DISH_FIELDS: Final = (
    "count",
    "dish_type",
    "taste_preferences",
    "cuisines",
    "effects",
    "special_populations",
    "required_ingredient_groups",
)
INGREDIENT_GROUP_FIELDS: Final = ("match", "items")
INGREDIENT_REQUIREMENT_FIELDS: Final = ("kind", "value")
CHANGE_ACTION_FIELDS: Final = ("field", "dish_index", "action", "evidence")

MEAL_PERIODS: Final = ("下午茶", "晚餐", "早餐", "午餐")
DISH_TYPES: Final = ("菜", "汤", "主食", "小菜", "未指定")
TASTE_PREFERENCES: Final = (
    "is_sweet",
    "is_light",
    "is_spicy",
    "is_salty",
    "is_sour",
)
CUISINES: Final = ("西餐风味", "东北菜", "粤菜", "川湘菜", "江浙菜")
EFFECTS: Final = ("助眠", "减脂", "养胃健胃消食", "贫血", "哺乳")
SPECIAL_POPULATIONS: Final = ("上班族", "儿童", "老人", "更年期")
INGREDIENT_GROUP_MATCHES: Final = ("all", "any")
INGREDIENT_REQUIREMENT_KINDS: Final = (
    "ingredient",
    "category",
    "concept",
)
INGREDIENT_CONCEPTS: Final = ("面",)
CHANGE_ACTIONS: Final = ("add", "replace", "remove")
CHANGEABLE_TOP_FIELDS: Final = (
    "meal_periods",
    "diner_count",
    "total_dish_count",
    "max_total_time_minutes",
    "max_difficulty",
    "available_ingredients",
)
SCALAR_FIELDS: Final = (
    "diner_count",
    "total_dish_count",
    "max_total_time_minutes",
)
SESSION_STATUSES: Final = (
    "in_progress",
    "needs_confirmation",
    "ready_for_planning",
)
MISSING_REQUIREMENTS: Final = ("人数", "明确菜品类型")


class DialogueConstraintExtractionError(Exception):
    """统一对话约束提取的可预期接口错误。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


INGREDIENT_REQUIREMENT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": list(INGREDIENT_REQUIREMENT_FIELDS),
    "properties": {
        "kind": {
            "type": "string",
            "enum": list(INGREDIENT_REQUIREMENT_KINDS),
        },
        "value": {"type": "string"},
    },
}

INGREDIENT_GROUP_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": list(INGREDIENT_GROUP_FIELDS),
    "properties": {
        "match": {
            "type": "string",
            "enum": list(INGREDIENT_GROUP_MATCHES),
        },
        "items": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": INGREDIENT_REQUIREMENT_SCHEMA,
        },
    },
}

DISH_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": list(DISH_FIELDS),
    "properties": {
        "count": {
            "type": ["integer", "null"],
            "minimum": 1,
        },
        "dish_type": {
            "type": "string",
            "enum": list(DISH_TYPES),
        },
        "taste_preferences": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                key: {"type": "boolean"} for key in TASTE_PREFERENCES
            },
        },
        "cuisines": {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "string", "enum": list(CUISINES)},
        },
        "effects": {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "string", "enum": list(EFFECTS)},
        },
        "special_populations": {
            "type": "array",
            "uniqueItems": True,
            "items": {
                "type": "string",
                "enum": list(SPECIAL_POPULATIONS),
            },
        },
        "required_ingredient_groups": {
            "type": "array",
            "uniqueItems": True,
            "items": INGREDIENT_GROUP_SCHEMA,
        },
    },
}

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
            "type": ["integer", "null"],
            "minimum": 0,
        },
        "action": {"type": "string", "enum": list(CHANGE_ACTIONS)},
        "evidence": {"type": "string"},
    },
}

CONSTRAINT_OUTPUT_SCHEMA: Final[dict[str, Any]] = {
    "title": "DialogueConstraintsTurnOutput",
    "description": "当前轮次提取出的完整新约束和相对上一状态的变更声明。",
    "type": "object",
    "additionalProperties": False,
    "required": list(TOP_LEVEL_FIELDS),
    "properties": {
        "dialogue_id": {"type": "integer", "minimum": 1},
        "meal_periods": {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "string", "enum": list(MEAL_PERIODS)},
        },
        "diner_count": {
            "type": ["integer", "null"],
            "minimum": 1,
        },
        "total_dish_count": {
            "type": ["integer", "null"],
            "minimum": 1,
        },
        "max_total_time_minutes": {
            "type": ["integer", "null"],
            "minimum": 1,
        },
        "max_difficulty": {
            "anyOf": [
                {"type": "string", "enum": ["简单", "中等"]},
                {"type": "null"},
            ]
        },
        "available_ingredients": {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "string"},
        },
        "dishes": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": DISH_SCHEMA,
        },
        "evidence": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
        "change_actions": {
            "type": "array",
            "items": CHANGE_ACTION_SCHEMA,
        },
    },
}


__all__ = [
    "CHANGEABLE_TOP_FIELDS",
    "CHANGE_ACTIONS",
    "CHANGE_ACTION_FIELDS",
    "CHANGE_ACTION_SCHEMA",
    "CONSTRAINT_OUTPUT_SCHEMA",
    "CUISINES",
    "DISH_FIELDS",
    "DISH_SCHEMA",
    "DISH_TYPES",
    "DialogueConstraintExtractionError",
    "EFFECTS",
    "INGREDIENT_CONCEPTS",
    "INGREDIENT_GROUP_FIELDS",
    "INGREDIENT_GROUP_MATCHES",
    "INGREDIENT_GROUP_SCHEMA",
    "INGREDIENT_REQUIREMENT_FIELDS",
    "INGREDIENT_REQUIREMENT_KINDS",
    "INGREDIENT_REQUIREMENT_SCHEMA",
    "MEAL_PERIODS",
    "MERGED_CONSTRAINT_FIELDS",
    "MISSING_REQUIREMENTS",
    "SCALAR_FIELDS",
    "SESSION_STATUSES",
    "SPECIAL_POPULATIONS",
    "TASTE_PREFERENCES",
    "TOP_LEVEL_FIELDS",
]
