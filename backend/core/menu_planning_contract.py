from __future__ import annotations

from decimal import Decimal
from typing import Literal, TypedDict


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
SCORED_NUTRIENT_FIELDS = NUTRIENT_FIELDS[:-1]


class NutritionValues(TypedDict):
    energy_kcal: Decimal
    protein_g: Decimal
    fat_g: Decimal
    carbohydrate_g: Decimal
    fiber_g: Decimal
    sodium_mg: Decimal
    calcium_mg: Decimal
    iron_mg: Decimal
    cholesterol_mg: Decimal


class NutrientTarget(TypedDict):
    status: Literal["available", "not_established"]
    unit: str
    target_value: Decimal | None
    lower_bound: Decimal | None
    upper_bound: Decimal | None
    target_basis: str | None
    lower_basis: str | None
    upper_basis: str | None


class PlanningCandidate(TypedDict):
    recipe_name: str
    recipe_type: str | None
    matched_tags: list[str]
    nutrition: NutritionValues


class PlanningDish(TypedDict):
    count: int | None
    dish_type: str
    candidates: list[PlanningCandidate]


class MenuPlanningInput(TypedDict):
    profile_id: int
    dialogue_id: int
    meal_period: str
    diner_count: int | None
    special_populations: list[str]
    dishes: list[PlanningDish]
    nutrient_targets: dict[str, NutrientTarget]
    unmatched_allergens: list[str]


class PlannedDish(TypedDict):
    dish_constraint_index: int
    recipe_name: str
    recipe_type: str | None
    matched_tags: list[str]
    nutrition: NutritionValues


class NutrientGrade(TypedDict):
    status: str
    actual_value: Decimal
    grade: Literal["excellent", "normal", "bad"] | None
    score: int | None


class MenuPlanningResult(TypedDict):
    profile_id: int
    dialogue_id: int
    meal_period: str
    diner_count: int
    selected_dishes: list[PlannedDish]
    total_nutrition: NutritionValues
    per_person_nutrition: NutritionValues
    nutrient_grades: dict[str, NutrientGrade]
    nutrition_score: int
    applied_health_constraints: list[str]
    unapplied_health_constraints: list[str]


class MenuPlanningError(Exception):
    """菜单规划的可预期接口错误。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


__all__ = [
    "MenuPlanningError",
    "MenuPlanningInput",
    "MenuPlanningResult",
    "NUTRIENT_FIELDS",
    "NutrientGrade",
    "NutrientTarget",
    "NutritionValues",
    "PlannedDish",
    "PlanningCandidate",
    "PlanningDish",
    "SCORED_NUTRIENT_FIELDS",
]
