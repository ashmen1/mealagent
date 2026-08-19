from __future__ import annotations

from typing import Any, NoReturn, cast

from backend.core.dish_filtering_contract import TAG_GROUPS, TAG_TO_GROUP
from backend.core.recommendation_reason_contract import (
    GRADE_LABELS,
    HealthConstraintReason,
    MAX_NUTRITION_SCORE,
    MenuReason,
    NutrientDetail,
    NutritionSummaryReason,
    ReasonSource,
    RecommendationReasonError,
    RecommendationReasonResult,
    SCORED_NUTRIENT_SPECS,
    TagMatchReason,
)
from backend.core.recommendation_reason_validation import (
    CandidateReference,
    NutrientGradeEvidence,
    PlanningEvidence,
    SelectedDishEvidence,
    validate_recommendation_reason_inputs,
    validate_selected_candidate_tags,
)


HEALTH_REASON_CONFIG = {
    "高血压": (
        "sodium_upper_bound",
        "考虑高血压需求，本桌菜单规划已将钠摄入上限作为必须满足的条件。",
    ),
    "高血糖": (
        "macronutrient_energy_ratio",
        "考虑高血糖需求，本桌菜单规划已将蛋白质、脂肪和碳水化合物的供能比范围作为必须满足的条件。",
    ),
}


class RecommendationReasonService:
    """将最终选菜、标签与整桌规划依据组装为固定推荐理由。"""

    def build(
        self,
        dish_filtering_result: object,
        menu_planning_result: object,
    ) -> RecommendationReasonResult:
        dishes, planning = validate_recommendation_reason_inputs(
            dish_filtering_result,
            menu_planning_result,
        )
        dish_recommendations = [
            _build_dish_recommendation(
                dishes,
                selected,
                selected_index,
            )
            for selected_index, selected in enumerate(
                planning["selected_dishes"]
            )
        ]
        return {
            "profile_id": planning["profile_id"],
            "dialogue_id": planning["dialogue_id"],
            "dish_recommendations": dish_recommendations,
            "menu_reasons": _build_menu_reasons(planning),
        }


def _build_dish_recommendation(
    dishes: list[list[CandidateReference]],
    selected: SelectedDishEvidence,
    selected_index: int,
) -> dict[str, Any]:
    dish_index = selected["dish_constraint_index"]
    recipe_name = selected["recipe_name"]
    if dish_index >= len(dishes):
        _internal(
            f"最终菜品无法回溯：组索引{dish_index}不存在，菜名{recipe_name}"
        )
    matches = [
        (candidate_index, candidate)
        for candidate_index, candidate in enumerate(dishes[dish_index])
        if candidate["recipe_name"] == recipe_name
    ]
    if len(matches) != 1:
        _internal(
            "最终菜品无法唯一回溯："
            f"组索引{dish_index}，菜名{recipe_name}，匹配数量{len(matches)}"
        )
    candidate_index, candidate = matches[0]
    candidate_location = (
        f"dish_filtering_result.dishes[{dish_index}][{candidate_index}]"
    )
    matched_tags, matched_groups = validate_selected_candidate_tags(
        candidate["value"],
        candidate_location,
    )
    return {
        "dish_constraint_index": dish_index,
        "recipe_name": recipe_name,
        "reasons": _build_tag_reasons(
            recipe_name,
            matched_tags,
            matched_groups,
            selected_index,
            dish_index,
            candidate_index,
        ),
    }


def _build_tag_reasons(
    recipe_name: str,
    matched_tags: list[str],
    matched_groups: list[str],
    selected_index: int,
    dish_index: int,
    candidate_index: int,
) -> list[TagMatchReason]:
    unknown_groups = [
        group for group in matched_groups if group not in TAG_GROUPS
    ]
    if unknown_groups:
        _internal("存在无固定模板的标签组：" + "、".join(unknown_groups))

    tags_by_group: dict[str, list[str]] = {}
    for tag in matched_tags:
        group = TAG_TO_GROUP.get(tag)
        if group is None:
            _internal(f"存在未知标签：{tag}")
        tags_by_group.setdefault(group, []).append(tag)

    if set(tags_by_group) != set(matched_groups):
        _internal("命中标签与标签组关系不一致")

    sources = _build_tag_sources(
        selected_index,
        dish_index,
        candidate_index,
    )
    return [
        {
            "reason_type": "tag_match",
            "matched_group": group,
            "matched_tags": list(tags_by_group[group]),
            "sources": [
                {
                    "component": source["component"],
                    "paths": list(source["paths"]),
                }
                for source in sources
            ],
            "text": _build_tag_text(
                recipe_name,
                group,
                tags_by_group[group],
            ),
        }
        for group in TAG_GROUPS
        if group in tags_by_group
    ]


def _build_tag_sources(
    selected_index: int,
    dish_index: int,
    candidate_index: int,
) -> list[ReasonSource]:
    return [
        {
            "component": "menu_planning",
            "paths": [
                f"selected_dishes[{selected_index}].dish_constraint_index",
                f"selected_dishes[{selected_index}].recipe_name",
            ],
        },
        {
            "component": "dish_filtering",
            "paths": [
                f"dishes[{dish_index}][{candidate_index}].matched_tags",
                f"dishes[{dish_index}][{candidate_index}].matched_groups",
            ],
        },
    ]


def _build_tag_text(
    recipe_name: str,
    group: str,
    tags: list[str],
) -> str:
    joined_tags = "、".join(tags)
    if group == "餐次":
        return f"{recipe_name}适合本次{joined_tags}。"
    if group == "口味":
        return f"{recipe_name}符合本次{joined_tags}口味偏好。"
    if group == "菜系":
        return f"{recipe_name}符合本次{joined_tags}偏好。"
    if group == "功效":
        return f"{recipe_name}匹配本次提出的{joined_tags}功效标签。"
    if group == "人群":
        return f"{recipe_name}匹配本次提出的{joined_tags}人群标签。"
    _internal(f"标签组缺少固定模板：{group}")


def _build_menu_reasons(planning: PlanningEvidence) -> list[MenuReason]:
    reasons: list[MenuReason] = [
        _build_health_reason(constraint, constraint_index)
        for constraint_index, constraint in enumerate(
            planning["applied_health_constraints"]
        )
    ]
    reasons.append(_build_nutrition_reason(planning))
    return reasons


def _build_health_reason(
    constraint: str,
    constraint_index: int,
) -> HealthConstraintReason:
    config = HEALTH_REASON_CONFIG.get(constraint)
    if config is None:
        _internal(f"健康约束缺少固定模板：{constraint}")
    rule, text = config
    return {
        "reason_type": "health_constraint",
        "constraint": constraint,
        "rule": cast(Any, rule),
        "sources": [
            {
                "component": "menu_planning",
                "paths": [
                    f"applied_health_constraints[{constraint_index}]"
                ],
            }
        ],
        "text": text,
    }


def _build_nutrition_reason(
    planning: PlanningEvidence,
) -> NutritionSummaryReason:
    grades = planning["nutrient_grades"]
    calculated_score = sum(
        grades[nutrient]["score"]
        for nutrient, _, _ in SCORED_NUTRIENT_SPECS
    )
    if calculated_score != planning["nutrition_score"]:
        _internal(
            "营养总分与八项分数之和不一致："
            f"总分{planning['nutrition_score']}，分项合计{calculated_score}"
        )
    return {
        "reason_type": "nutrition_summary",
        "nutrition_score": planning["nutrition_score"],
        "max_score": MAX_NUTRITION_SCORE,
        "nutrient_details": _build_nutrient_details(grades),
        "sources": [
            {
                "component": "menu_planning",
                "paths": ["nutrition_score"],
            }
        ],
        "text": _build_nutrition_text(
            planning["nutrition_score"],
            grades,
        ),
    }


def _build_nutrient_details(
    grades: dict[str, NutrientGradeEvidence],
) -> list[NutrientDetail]:
    return [
        {
            "nutrient": nutrient,
            "label": label,
            "menu_total_value": grades[nutrient]["actual_value"],
            "unit": unit,
            "grade": cast(Any, grades[nutrient]["grade"]),
            "grade_label": GRADE_LABELS[grades[nutrient]["grade"]],
            "score": grades[nutrient]["score"],
            "source": {
                "component": "menu_planning",
                "paths": [f"nutrient_grades.{nutrient}"],
            },
        }
        for nutrient, label, unit in SCORED_NUTRIENT_SPECS
    ]


def _build_nutrition_text(
    nutrition_score: int,
    grades: dict[str, NutrientGradeEvidence],
) -> str:
    text = (
        "本桌菜单按8项营养指标评分，满分16分，"
        f"本桌得{nutrition_score}分。"
    )
    excellent = [
        label
        for nutrient, label, _ in SCORED_NUTRIENT_SPECS
        if grades[nutrient]["grade"] == "excellent"
    ]
    normal = [
        label
        for nutrient, label, _ in SCORED_NUTRIENT_SPECS
        if grades[nutrient]["grade"] == "normal"
    ]
    clauses = []
    if excellent:
        clauses.append(f"{'、'.join(excellent)}处于优秀区间（每项2分）")
    if normal:
        clauses.append(f"{'、'.join(normal)}处于正常区间（每项1分）")
    if clauses:
        text += "；".join(clauses) + "。"
    return text


def _internal(message: str) -> NoReturn:
    raise RecommendationReasonError(500, message)


__all__ = ["RecommendationReasonError", "RecommendationReasonService"]
