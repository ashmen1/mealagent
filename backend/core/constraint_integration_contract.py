from __future__ import annotations

from typing import Literal, TypedDict


class IngredientRequirement(TypedDict):
    """菜品所需的食材、类别或概念。"""

    kind: str
    value: str


class IntegratedDish(TypedDict):
    """健康档案与单轮对话整合后的单组菜品约束。"""

    count: int | None
    dish_type: str
    taste_preferences: dict[str, bool]
    cuisines: list[str]
    effects: list[str]
    special_populations: list[str]
    required_ingredients: list[IngredientRequirement]


class ConstraintConflict(TypedDict):
    """需要后续用户确认的同名过敏与必需食材冲突。"""

    code: Literal["allergen_required_ingredient"]
    dish_index: int
    profile_path: str
    dialogue_path: str
    allergen: str
    required_ingredient: IngredientRequirement
    dialogue_evidence: str


class IntegratedConstraints(TypedDict):
    """供后续过滤与菜单编排使用的统一约束。"""

    profile_id: int
    dialogue_id: int
    meal_periods: list[str]
    diner_count: int | None
    total_dish_count: int | None
    max_total_time_minutes: int | None
    max_difficulty: Literal["简单", "中等"] | None
    available_ingredients: list[str]
    allergens: list[str]
    dishes: list[IntegratedDish]
    has_conflicts: bool
    conflicts: list[ConstraintConflict]


class ConstraintIntegrationError(Exception):
    """约束整合的可预期接口错误。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class ConstraintIntegrationValidationError(ConstraintIntegrationError):
    """输入不符合 Spec_01 或 Spec_02 输出契约。"""

    def __init__(self, message: str) -> None:
        super().__init__(400, message)


__all__ = [
    "ConstraintConflict",
    "ConstraintIntegrationError",
    "ConstraintIntegrationValidationError",
    "IngredientRequirement",
    "IntegratedConstraints",
    "IntegratedDish",
]
