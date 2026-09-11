from __future__ import annotations

import copy
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, NoReturn, TypedDict, cast

from backend.core.dish_filtering_contract import (
    DishFilteringValidationError,
    INTEGRATED_TOP_LEVEL_FIELDS,
)
from backend.core.constraint_integration_contract import (
    ConstraintIntegrationValidationError,
)
from backend.core.dish_filtering_validation import (
    validate_integrated_constraints,
)
from backend.core.recommendation_reason_contract import (
    GRADE_SCORES,
    GradeName,
    MAX_NUTRITION_SCORE,
    RecommendationReasonError,
    SCORED_NUTRIENT_SPECS,
)


class CandidateReference(TypedDict):
    recipe_name: str
    raw_candidate: Mapping[str, Any]


class SelectedDishEvidence(TypedDict):
    dish_constraint_index: int
    recipe_name: str


class NutrientGradeEvidence(TypedDict):
    actual_value: Decimal
    grade: GradeName
    score: int


class PlanningEvidence(TypedDict):
    profile_id: int
    dialogue_id: int
    diner_count: int
    selected_dishes: list[SelectedDishEvidence]
    nutrition_score: int
    nutrient_grades: dict[str, NutrientGradeEvidence]
    applied_health_constraints: list[str]


class CandidateAttemptEvidence(TypedDict):
    candidate_limit: int | None
    candidate_counts: list[int]
    outcome: str
    nutrition_score: int | None


class DecisionEvidence(TypedDict):
    effective_constraints: dict[str, Any]
    candidate_attempts: list[CandidateAttemptEvidence]


def validate_recommendation_reason_inputs(
    dish_filtering_result: object,
    menu_planning_result: object,
    decision_context: object,
) -> tuple[
    list[list[CandidateReference]],
    PlanningEvidence,
    DecisionEvidence,
]:
    """校验并复制推荐理由真正消费的上游字段。"""

    filtering = _require_mapping(
        dish_filtering_result,
        "dish_filtering_result",
    )
    dishes = _validate_dishes(
        _required(filtering, "dishes", "dish_filtering_result")
    )
    planning = _validate_planning_result(menu_planning_result)
    decision = _validate_decision_context(decision_context)
    _validate_cross_input_consistency(dishes, planning, decision)
    return dishes, planning, decision


def _validate_planning_result(value: object) -> PlanningEvidence:
    planning = _require_mapping(value, "menu_planning_result")
    profile_id = _validate_positive_integer(
        _required(planning, "profile_id", "menu_planning_result"),
        "menu_planning_result.profile_id",
    )
    dialogue_id = _validate_positive_integer(
        _required(planning, "dialogue_id", "menu_planning_result"),
        "menu_planning_result.dialogue_id",
    )
    diner_count = _validate_positive_integer(
        _required(planning, "diner_count", "menu_planning_result"),
        "menu_planning_result.diner_count",
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
    return {
        "profile_id": profile_id,
        "dialogue_id": dialogue_id,
        "diner_count": diner_count,
        "selected_dishes": selected_dishes,
        "nutrition_score": nutrition_score,
        "nutrient_grades": nutrient_grades,
        "applied_health_constraints": health_constraints,
    }


def _validate_decision_context(value: object) -> DecisionEvidence:
    context = _require_mapping(value, "decision_context")
    raw_constraints = _require_mapping(
        _required(context, "effective_constraints", "decision_context"),
        "decision_context.effective_constraints",
    )
    normalized_constraints = {
        field: copy.deepcopy(
            _required(
                raw_constraints,
                field,
                "decision_context.effective_constraints",
            )
        )
        for field in INTEGRATED_TOP_LEVEL_FIELDS
    }
    try:
        validate_integrated_constraints(normalized_constraints)
    except (
        ConstraintIntegrationValidationError,
        DishFilteringValidationError,
    ) as exc:
        _invalid(str(exc))
    attempts = _validate_candidate_attempts(
        _required(context, "candidate_attempts", "decision_context")
    )
    return {
        "effective_constraints": normalized_constraints,
        "candidate_attempts": attempts,
    }


def _validate_candidate_attempts(
    value: object,
) -> list[CandidateAttemptEvidence]:
    if not isinstance(value, list) or not value:
        _invalid("decision_context.candidate_attempts必须是非空数组")
    attempts: list[CandidateAttemptEvidence] = []
    seen_limits: set[int | None] = set()
    for index, raw_value in enumerate(value):
        location = f"decision_context.candidate_attempts[{index}]"
        raw = _require_mapping(raw_value, location)
        candidate_limit = _required(raw, "candidate_limit", location)
        if candidate_limit is not None and (
            type(candidate_limit) is not int
            or candidate_limit not in {100, 300}
        ):
            _invalid(f"{location}.candidate_limit必须为100、300或null")
        candidate_counts = _validate_candidate_counts(
            _required(raw, "candidate_counts", location),
            f"{location}.candidate_counts",
        )
        outcome = _required(raw, "outcome", location)
        if not isinstance(outcome, str) or outcome not in {
            "infeasible",
            "below_target",
            "accepted",
        }:
            _invalid(f"{location}.outcome无效")
        nutrition_score = _required(raw, "nutrition_score", location)
        if outcome == "infeasible":
            if nutrition_score is not None:
                _invalid(f"{location}.nutrition_score必须为null")
        else:
            nutrition_score = _validate_nutrition_score(nutrition_score)
            if outcome == "below_target" and nutrition_score >= 8:
                _invalid(f"{location}.below_target得分必须低于8")
        if candidate_limit in seen_limits:
            _invalid("decision_context.candidate_attempts不得重复候选阶段")
        seen_limits.add(cast(int | None, candidate_limit))
        attempt: CandidateAttemptEvidence = {
            "candidate_limit": cast(int | None, candidate_limit),
            "candidate_counts": candidate_counts,
            "outcome": cast(str, outcome),
            "nutrition_score": cast(int | None, nutrition_score),
        }
        attempts.append(attempt)
    return attempts


def _validate_candidate_counts(value: object, location: str) -> list[int]:
    if not isinstance(value, list) or not value:
        _invalid(f"{location}必须是非空整数数组")
    if any(type(item) is not int or item < 0 for item in value):
        _invalid(f"{location}只能包含非负整数")
    return list(value)


def _validate_cross_input_consistency(
    dishes: list[list[CandidateReference]],
    planning: PlanningEvidence,
    decision: DecisionEvidence,
) -> None:
    constraints = decision["effective_constraints"]
    if constraints["profile_id"] != planning["profile_id"]:
        _internal("生效约束profile_id与菜单规划结果不一致")
    if constraints["dialogue_id"] != planning["dialogue_id"]:
        _internal("生效约束dialogue_id与菜单规划结果不一致")
    if (
        constraints["diner_count"] is not None
        and constraints["diner_count"] != planning["diner_count"]
    ):
        _internal("生效约束人数与菜单规划结果不一致")
    if len(constraints["dishes"]) != len(dishes):
        _internal("生效约束菜品组数与筛选结果不一致")

    for selected in planning["selected_dishes"]:
        dish_index = selected["dish_constraint_index"]
        if dish_index >= len(dishes):
            _internal("最终菜品引用的约束组不存在")

    expected_stages = _expected_candidate_stages(dishes)
    attempts = decision["candidate_attempts"]
    if len(attempts) > len(expected_stages):
        _internal("候选尝试数量超过真实候选阶段")
    for index, attempt in enumerate(attempts):
        expected_limit, expected_counts = expected_stages[index]
        if attempt["candidate_limit"] != expected_limit:
            _internal("候选尝试上限与真实候选阶段不一致")
        if attempt["candidate_counts"] != expected_counts:
            _internal("候选尝试数量与真实候选数量不一致")
        is_last = index == len(attempts) - 1
        if not is_last and attempt["outcome"] == "accepted":
            _internal("候选尝试在已接受后仍继续执行")
        if (
            attempt["outcome"] == "accepted"
            and attempt["candidate_limit"] is not None
            and cast(int, attempt["nutrition_score"]) < 8
        ):
            _internal("非全量候选未达到目标却被接受")
    final_attempt = attempts[-1]
    if final_attempt["outcome"] != "accepted":
        _internal("推荐成功时最后一次候选尝试必须被接受")
    if final_attempt["nutrition_score"] != planning["nutrition_score"]:
        _internal("候选尝试最终得分与菜单规划结果不一致")
def _expected_candidate_stages(
    dishes: list[list[CandidateReference]],
) -> list[tuple[int | None, list[int]]]:
    full_counts = [len(group) for group in dishes]
    stages: list[tuple[int | None, list[int]]] = []
    for limit in (100, 300):
        counts = [min(limit, count) for count in full_counts]
        if counts == full_counts:
            stages.append((None, counts))
            return stages
        stages.append((limit, counts))
    stages.append((None, full_counts))
    return stages


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
            group.append(
                {
                    "recipe_name": recipe_name,
                    "raw_candidate": candidate,
                }
            )
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
        grade_name = cast(GradeName, grade)
        if GRADE_SCORES[grade_name] != score:
            _invalid(f"{location}的等级与分数不对应")
        result[nutrient] = {
            "actual_value": actual_value,
            "grade": grade_name,
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


def _internal(message: str) -> NoReturn:
    raise RecommendationReasonError(500, message)


__all__ = [
    "CandidateReference",
    "CandidateAttemptEvidence",
    "DecisionEvidence",
    "NutrientGradeEvidence",
    "PlanningEvidence",
    "SelectedDishEvidence",
    "validate_recommendation_reason_inputs",
    "validate_selected_candidate_tags",
]
