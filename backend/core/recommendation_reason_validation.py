from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any, NoReturn, TypedDict

from backend.core.recommendation_reason_contract import (
    GRADE_SCORES,
    MAX_NUTRITION_SCORE,
    RecommendationReasonError,
    SCORED_NUTRIENT_SPECS,
)


class CandidateReference(TypedDict):
    recipe_name: str
    value: Mapping[str, Any]


class SelectedDishEvidence(TypedDict):
    dish_constraint_index: int
    recipe_name: str


class NutrientGradeEvidence(TypedDict):
    actual_value: Decimal
    grade: str
    score: int


class PlanningEvidence(TypedDict):
    profile_id: int
    dialogue_id: int
    selected_dishes: list[SelectedDishEvidence]
    nutrition_score: int
    nutrient_grades: dict[str, NutrientGradeEvidence]
    applied_health_constraints: list[str]


def validate_recommendation_reason_inputs(
    dish_filtering_result: object,
    menu_planning_result: object,
) -> tuple[list[list[CandidateReference]], PlanningEvidence]:
    """校验并复制推荐理由真正消费的上游字段。"""

    filtering = _require_mapping(
        dish_filtering_result,
        "dish_filtering_result",
    )
    planning = _require_mapping(
        menu_planning_result,
        "menu_planning_result",
    )
    dishes = _validate_dishes(
        _required(filtering, "dishes", "dish_filtering_result")
    )
    profile_id = _validate_positive_integer(
        _required(planning, "profile_id", "menu_planning_result"),
        "menu_planning_result.profile_id",
    )
    dialogue_id = _validate_positive_integer(
        _required(planning, "dialogue_id", "menu_planning_result"),
        "menu_planning_result.dialogue_id",
    )
    selected_dishes = _validate_selected_dishes(
        _required(planning, "selected_dishes", "menu_planning_result")
    )
    nutrition_score = _validate_nutrition_score(
        _required(planning, "nutrition_score", "menu_planning_result")
    )
    nutrient_grades = _validate_nutrient_grades(
        _required(planning, "nutrient_grades", "menu_planning_result")
    )
    health_constraints = _validate_string_array(
        _required(
            planning,
            "applied_health_constraints",
            "menu_planning_result",
        ),
        "menu_planning_result.applied_health_constraints",
    )
    return dishes, {
        "profile_id": profile_id,
        "dialogue_id": dialogue_id,
        "selected_dishes": selected_dishes,
        "nutrition_score": nutrition_score,
        "nutrient_grades": nutrient_grades,
        "applied_health_constraints": health_constraints,
    }


def validate_selected_candidate_tags(
    value: Mapping[str, Any],
    location: str,
) -> tuple[list[str], list[str]]:
    """只校验最终被选中候选的标签证据。"""

    matched_tags = _validate_string_array(
        _required(value, "matched_tags", location),
        f"{location}.matched_tags",
    )
    matched_groups = _validate_string_array(
        _required(value, "matched_groups", location),
        f"{location}.matched_groups",
    )
    return matched_tags, matched_groups


def _validate_dishes(value: object) -> list[list[CandidateReference]]:
    if not isinstance(value, list):
        _invalid("dish_filtering_result.dishes必须是数组")
    dishes: list[list[CandidateReference]] = []
    for dish_index, group_value in enumerate(value):
        group_location = f"dish_filtering_result.dishes[{dish_index}]"
        if not isinstance(group_value, list):
            _invalid(f"{group_location}必须是候选数组")
        group: list[CandidateReference] = []
        for candidate_index, candidate_value in enumerate(group_value):
            location = f"{group_location}[{candidate_index}]"
            candidate = _require_mapping(candidate_value, location)
            recipe_name = _validate_nonempty_string(
                _required(candidate, "recipe_name", location),
                f"{location}.recipe_name",
            )
            group.append({"recipe_name": recipe_name, "value": candidate})
        dishes.append(group)
    return dishes


def _validate_selected_dishes(value: object) -> list[SelectedDishEvidence]:
    if not isinstance(value, list) or not value:
        _invalid("menu_planning_result.selected_dishes必须是非空数组")
    selected_dishes: list[SelectedDishEvidence] = []
    recipe_names: set[str] = set()
    for selected_index, selected_value in enumerate(value):
        location = f"menu_planning_result.selected_dishes[{selected_index}]"
        selected = _require_mapping(selected_value, location)
        dish_index = _validate_nonnegative_integer(
            _required(selected, "dish_constraint_index", location),
            f"{location}.dish_constraint_index",
        )
        recipe_name = _validate_nonempty_string(
            _required(selected, "recipe_name", location),
            f"{location}.recipe_name",
        )
        if recipe_name in recipe_names:
            _invalid("menu_planning_result.selected_dishes菜名不得重复")
        recipe_names.add(recipe_name)
        selected_dishes.append(
            {
                "dish_constraint_index": dish_index,
                "recipe_name": recipe_name,
            }
        )
    return selected_dishes


def _validate_nutrition_score(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_NUTRITION_SCORE:
        _invalid("menu_planning_result.nutrition_score必须是0到16的整数")
    return value


def _validate_nutrient_grades(
    value: object,
) -> dict[str, NutrientGradeEvidence]:
    grades = _require_mapping(value, "menu_planning_result.nutrient_grades")
    result: dict[str, NutrientGradeEvidence] = {}
    for nutrient, _, _ in SCORED_NUTRIENT_SPECS:
        location = f"menu_planning_result.nutrient_grades.{nutrient}"
        grade_value = _require_mapping(
            _required(grades, nutrient, "menu_planning_result.nutrient_grades"),
            location,
        )
        actual_value = _required(grade_value, "actual_value", location)
        if (
            not isinstance(actual_value, Decimal)
            or not actual_value.is_finite()
            or actual_value < 0
        ):
            _invalid(f"{location}.actual_value必须是非负Decimal")
        grade = _required(grade_value, "grade", location)
        score = _required(grade_value, "score", location)
        if (
            not isinstance(grade, str)
            or grade not in GRADE_SCORES
            or type(score) is not int
        ):
            _invalid(f"{location}的等级或分数非法")
        if GRADE_SCORES[grade] != score:
            _invalid(f"{location}的等级与分数不对应")
        result[nutrient] = {
            "actual_value": actual_value,
            "grade": grade,
            "score": score,
        }
    return result


def _validate_string_array(value: object, location: str) -> list[str]:
    if not isinstance(value, list):
        _invalid(f"{location}必须是数组")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        _invalid(f"{location}只能包含非空字符串")
    if len(set(value)) != len(value):
        _invalid(f"{location}不得重复")
    return list(value)


def _validate_positive_integer(value: object, location: str) -> int:
    if type(value) is not int or value <= 0:
        _invalid(f"{location}必须是正整数")
    return value


def _validate_nonnegative_integer(value: object, location: str) -> int:
    if type(value) is not int or value < 0:
        _invalid(f"{location}必须是非负整数")
    return value


def _validate_nonempty_string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid(f"{location}必须是非空字符串")
    return value


def _require_mapping(value: object, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _invalid(f"{location}必须是对象")
    return value


def _required(
    value: Mapping[str, Any],
    field: str,
    location: str,
) -> object:
    if field not in value:
        _invalid(f"{location}缺少{field}")
    return value[field]


def _invalid(message: str) -> NoReturn:
    raise RecommendationReasonError(400, message)


__all__ = [
    "CandidateReference",
    "NutrientGradeEvidence",
    "PlanningEvidence",
    "SelectedDishEvidence",
    "validate_recommendation_reason_inputs",
    "validate_selected_candidate_tags",
]
