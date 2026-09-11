from __future__ import annotations

from typing import Final, NoReturn

from backend.core.dish_filtering_contract import TAG_GROUPS, TAG_TO_GROUP
from backend.core.recommendation_reason_contract import (
    GRADE_LABELS,
    DishRecommendation,
    HealthConstraintReason,
    HealthRule,
    MAX_NUTRITION_SCORE,
    MenuReason,
    NutrientDetail,
    NutritionSummaryReason,
    RecommendationReasonError,
    RecommendationReasonResult,
    SCORED_NUTRIENT_SPECS,
    TagMatchReason,
)
from backend.core.recommendation_reason_validation import (
    NutrientGradeEvidence,
    PlanningEvidence,
    validate_recommendation_reason_inputs,
)
from backend.services.recommendation_reason_evidence import (
    SelectedCandidateEvidence,
    build_tag_sources,
    build_tag_text,
    collect_selected_evidence,
)
from backend.services.recommendation_reason_filtering import (
    build_filtering_reasons,
)
from backend.services.recommendation_reason_planning import (
    build_planning_reasons,
)


HEALTH_REASON_CONFIG: Final[dict[str, tuple[HealthRule, str]]] = {
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
    """将最终选菜、筛选与整桌规划依据组装为固定推荐理由。"""

    def build(
        self,
        dish_filtering_result: object,
        menu_planning_result: object,
        decision_context: object,
    ) -> RecommendationReasonResult:
        dishes, planning, decision = validate_recommendation_reason_inputs(
            dish_filtering_result,
            menu_planning_result,
            decision_context,
        )
        selected = collect_selected_evidence(dishes, planning)
        return {
            "profile_id": planning["profile_id"],
            "dialogue_id": planning["dialogue_id"],
            "dish_recommendations": [
                _build_dish_recommendation(item) for item in selected
            ],
            "filtering_reasons": build_filtering_reasons(
                selected,
                decision,
            ),
            "planning_reasons": build_planning_reasons(
                selected,
                planning,
                decision,
            ),
            "menu_reasons": _build_menu_reasons(planning),
        }


def _build_dish_recommendation(
    evidence: SelectedCandidateEvidence,
) -> DishRecommendation:
    return {
        "dish_constraint_index": evidence["dish_index"],
        "recipe_name": evidence["recipe_name"],
        "reasons": _build_tag_reasons(evidence),
    }


def _build_tag_reasons(
    evidence: SelectedCandidateEvidence,
) -> list[TagMatchReason]:
    matched_tags = evidence["matched_tags"]
    matched_groups = evidence["matched_groups"]
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

    reasons: list[TagMatchReason] = []
    for group in TAG_GROUPS:
        tags = tags_by_group.get(group)
        if tags is None:
            continue
        reasons.append(
            {
                "reason_type": "tag_match",
                "matched_group": group,
                "matched_tags": list(tags),
                "sources": build_tag_sources(evidence),
                "text": build_tag_text(
                    [evidence["recipe_name"]],
                    group,
                    tags,
                ),
            }
        )
    return reasons


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
        "rule": rule,
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
            "grade": grades[nutrient]["grade"],
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
    clauses: list[str] = []
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
