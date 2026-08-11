from __future__ import annotations

from decimal import Decimal
from typing import TypedDict


NUTRIENT_FIELDS = (
    "energy_kcal",
    "protein_g",
    "fat_g",
    "carbohydrate_g",
    "fiber_g",
    "sodium_mg",
    "calcium_mg",
    "iron_mg",
    "cholesterol_mg",
)
MEAL_PERIODS = ("早餐", "午餐", "晚餐")


class RecipeNutritionResult(TypedDict):
    recipe_name: str
    energy_kcal: Decimal
    protein_g: Decimal
    fat_g: Decimal
    carbohydrate_g: Decimal
    fiber_g: Decimal
    sodium_mg: Decimal
    calcium_mg: Decimal
    iron_mg: Decimal
    cholesterol_mg: Decimal


class NutrientTargetResult(TypedDict):
    status: str
    unit: str
    target_value: Decimal | None
    lower_bound: Decimal | None
    upper_bound: Decimal | None
    target_basis: str | None
    lower_basis: str | None
    upper_basis: str | None


class MealNutritionTargetsResult(TypedDict):
    profile_id: int
    meal_period: str
    nutrients: dict[str, NutrientTargetResult]


class NutritionCalculationError(Exception):
    """营养计算查询的可预期接口错误。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def validate_recipe_names(recipe_names: object) -> list[str]:
    if not isinstance(recipe_names, list) or not recipe_names:
        raise NutritionCalculationError(400, "recipe_names 必须是非空数组")
    if any(not isinstance(name, str) or not name.strip() for name in recipe_names):
        raise NutritionCalculationError(400, "recipe_names 只能包含非空字符串")
    if len(set(recipe_names)) != len(recipe_names):
        raise NutritionCalculationError(400, "recipe_names 不得重复")
    return list(recipe_names)


def validate_profile_and_meal(profile_id: object, meal_period: object) -> tuple[int, str]:
    if isinstance(profile_id, bool) or not isinstance(profile_id, int) or profile_id <= 0:
        raise NutritionCalculationError(400, "profile_id 必须是正整数")
    if meal_period not in MEAL_PERIODS:
        raise NutritionCalculationError(400, "meal_period 只允许早餐、午餐、晚餐")
    return profile_id, str(meal_period)


__all__ = [
    "MEAL_PERIODS",
    "NUTRIENT_FIELDS",
    "MealNutritionTargetsResult",
    "NutrientTargetResult",
    "NutritionCalculationError",
    "RecipeNutritionResult",
    "validate_profile_and_meal",
    "validate_recipe_names",
]
