from __future__ import annotations

from typing import Any, Final


TOP_LEVEL_FIELDS: Final = (
    "dialogue_id",
    "meal_periods",
    "diner_count",
    "max_total_time_minutes",
    "available_ingredients",
    "dishes",
    "evidence",
)
DISH_FIELDS: Final = (
    "count",
    "dish_type",
    "taste_preferences",
    "cuisines",
    "effects",
    "special_populations",
    "required_ingredients",
)
INGREDIENT_REQUIREMENT_FIELDS: Final = ("kind", "value")

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
INGREDIENT_REQUIREMENT_KINDS: Final = (
    "ingredient",
    "category",
    "concept",
)
INGREDIENT_CONCEPTS: Final = ("面",)


class DialogueConstraintExtractionError(Exception):
    """单轮对话约束提取的可预期接口错误。"""

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

DISH_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": list(DISH_FIELDS),
    "properties": {
        "count": {
            "anyOf": [
                {"type": "integer", "minimum": 1},
                {"type": "null"},
            ]
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
        "required_ingredients": {
            "type": "array",
            "uniqueItems": True,
            "items": INGREDIENT_REQUIREMENT_SCHEMA,
        },
    },
}

CONSTRAINT_OUTPUT_SCHEMA: Final[dict[str, Any]] = {
    "title": "SingleTurnDialogueConstraints",
    "description": "单轮中文对话中提取出的整餐约束和菜品约束。",
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
            "anyOf": [
                {"type": "integer", "minimum": 1},
                {"type": "null"},
            ]
        },
        "max_total_time_minutes": {
            "anyOf": [
                {"type": "integer", "minimum": 1},
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
    },
}


__all__ = [
    "CONSTRAINT_OUTPUT_SCHEMA",
    "CUISINES",
    "DISH_FIELDS",
    "DISH_TYPES",
    "DialogueConstraintExtractionError",
    "EFFECTS",
    "INGREDIENT_CONCEPTS",
    "INGREDIENT_REQUIREMENT_FIELDS",
    "INGREDIENT_REQUIREMENT_KINDS",
    "MEAL_PERIODS",
    "SPECIAL_POPULATIONS",
    "TASTE_PREFERENCES",
    "TOP_LEVEL_FIELDS",
]
