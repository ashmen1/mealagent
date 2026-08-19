from __future__ import annotations

from decimal import Decimal
from typing import Any


NUTRIENTS = (
    ("energy_kcal", "能量", "kcal"),
    ("protein_g", "蛋白质", "g"),
    ("fat_g", "脂肪", "g"),
    ("carbohydrate_g", "碳水化合物", "g"),
    ("fiber_g", "膳食纤维", "g"),
    ("sodium_mg", "钠", "mg"),
    ("calcium_mg", "钙", "mg"),
    ("iron_mg", "铁", "mg"),
)

GRADE_VALUES = {
    "excellent": ("优秀区间", 2),
    "normal": ("正常区间", 1),
    "bad": ("正常区间外", 0),
}


def build_candidate(
    recipe_name: str = "白灼芥蓝",
    matched_tags: list[str] | None = None,
    matched_groups: list[str] | None = None,
    **extras: Any,
) -> dict[str, Any]:
    candidate = {
        "recipe_name": recipe_name,
        "recipe_type": "菜",
        "matched_tags": list(
            matched_tags
            if matched_tags is not None
            else ["晚餐", "清淡", "粤菜"]
        ),
        "matched_groups": list(
            matched_groups
            if matched_groups is not None
            else ["餐次", "口味", "菜系"]
        ),
    }
    candidate.update(extras)
    return candidate


def build_filtering_result(
    dishes: list[list[dict[str, Any]]] | None = None,
    **extras: Any,
) -> dict[str, Any]:
    result = {
        "dishes": dishes if dishes is not None else [[build_candidate()]],
        "unmatched_allergens": [],
    }
    result.update(extras)
    return result


def build_grade(
    actual_value: Decimal = Decimal("10.125"),
    grade: str = "excellent",
    score: int | None = None,
) -> dict[str, Any]:
    expected_score = GRADE_VALUES[grade][1] if score is None else score
    return {
        "status": "available",
        "actual_value": actual_value,
        "grade": grade,
        "score": expected_score,
    }


def build_nutrient_grades(
    grades: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    selected_grades = grades or {
        "energy_kcal": "excellent",
        "protein_g": "excellent",
        "fat_g": "normal",
        "carbohydrate_g": "normal",
        "fiber_g": "bad",
        "sodium_mg": "excellent",
        "calcium_mg": "normal",
        "iron_mg": "bad",
    }
    result = {
        nutrient: build_grade(
            actual_value=Decimal(f"{index + 1}.1230"),
            grade=selected_grades[nutrient],
        )
        for index, (nutrient, _, _) in enumerate(NUTRIENTS)
    }
    result["cholesterol_mg"] = {
        "status": "not_established",
        "actual_value": Decimal("9.876"),
        "grade": None,
        "score": None,
    }
    return result


def calculate_score(grades: dict[str, dict[str, Any]]) -> int:
    return sum(int(grades[nutrient]["score"]) for nutrient, _, _ in NUTRIENTS)


def build_selected_dish(
    dish_constraint_index: int = 0,
    recipe_name: str = "白灼芥蓝",
    **extras: Any,
) -> dict[str, Any]:
    selected = {
        "dish_constraint_index": dish_constraint_index,
        "recipe_name": recipe_name,
        "recipe_type": "菜",
        # 该字段是菜单规划阶段携带的副本，推荐理由不得读取或比较。
        "matched_tags": ["不参与推荐理由"],
        "nutrition": {},
    }
    selected.update(extras)
    return selected


def build_planning_result(
    selected_dishes: list[dict[str, Any]] | None = None,
    nutrient_grades: dict[str, dict[str, Any]] | None = None,
    nutrition_score: int | None = None,
    applied_health_constraints: list[str] | None = None,
    **extras: Any,
) -> dict[str, Any]:
    grades = nutrient_grades or build_nutrient_grades()
    result = {
        "profile_id": 25,
        "dialogue_id": 101,
        "meal_period": "晚餐",
        "diner_count": 2,
        "selected_dishes": (
            selected_dishes
            if selected_dishes is not None
            else [build_selected_dish()]
        ),
        "nutrition_score": (
            calculate_score(grades)
            if nutrition_score is None
            else nutrition_score
        ),
        "nutrient_grades": grades,
        "applied_health_constraints": (
            list(applied_health_constraints)
            if applied_health_constraints is not None
            else []
        ),
        "unapplied_health_constraints": ["备孕"],
    }
    result.update(extras)
    return result


def expected_nutrient_details(
    grades: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "nutrient": nutrient,
            "label": label,
            "menu_total_value": grades[nutrient]["actual_value"],
            "unit": unit,
            "grade": grades[nutrient]["grade"],
            "grade_label": GRADE_VALUES[grades[nutrient]["grade"]][0],
            "score": grades[nutrient]["score"],
            "source": {
                "component": "menu_planning",
                "paths": [f"nutrient_grades.{nutrient}"],
            },
        }
        for nutrient, label, unit in NUTRIENTS
    ]


__all__ = [
    "GRADE_VALUES",
    "NUTRIENTS",
    "build_candidate",
    "build_filtering_result",
    "build_grade",
    "build_nutrient_grades",
    "build_planning_result",
    "build_selected_dish",
    "calculate_score",
    "expected_nutrient_details",
]
