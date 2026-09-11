from __future__ import annotations

import copy
import json
from typing import Any, Final, NoReturn

from backend.core.dish_filtering_contract import (
    TAG_GROUPS,
    TAG_TO_GROUP,
    TASTE_KEY_TO_TAG,
)
from backend.core.recommendation_reason_contract import (
    GRADE_LABELS,
    DishRecommendation,
    FilteringReason,
    FilteringRule,
    HealthConstraintReason,
    HealthRule,
    MAX_NUTRITION_SCORE,
    MenuReason,
    NutrientDetail,
    NutritionSummaryReason,
    PlanningReason,
    PlanningRule,
    ReasonSource,
    RecommendationReasonError,
    RecommendationReasonResult,
    SCORED_NUTRIENT_SPECS,
    TagMatchReason,
)
from backend.core.recommendation_reason_validation import (
    CandidateReference,
    CandidateAttemptEvidence,
    DecisionEvidence,
    NutrientGradeEvidence,
    PlanningEvidence,
    SelectedDishEvidence,
    validate_recommendation_reason_inputs,
    validate_selected_candidate_tags,
)


TAG_TEXT_TEMPLATES: Final[dict[str, str]] = {
    "餐次": "{recipe_name}适合本次{tags}。",
    "口味": "{recipe_name}符合本次{tags}口味偏好。",
    "菜系": "{recipe_name}符合本次{tags}偏好。",
    "功效": "{recipe_name}匹配本次提出的{tags}功效标签。",
    "人群": "{recipe_name}匹配本次提出的{tags}人群标签。",
}

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
    """将最终选菜、标签与整桌规划依据组装为固定推荐理由。"""

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
            "filtering_reasons": _build_filtering_reasons(
                dishes,
                planning,
                decision,
            ),
            "planning_reasons": _build_planning_reasons(
                planning,
                decision,
            ),
            "menu_reasons": _build_menu_reasons(planning),
        }


def _build_dish_recommendation(
    dishes: list[list[CandidateReference]],
    selected: SelectedDishEvidence,
    selected_index: int,
) -> DishRecommendation:
    dish_index = selected["dish_constraint_index"]
    recipe_name = selected["recipe_name"]
    candidate_index, candidate = _find_selected_candidate(
        dishes,
        dish_index,
        recipe_name,
    )
    candidate_location = (
        f"dish_filtering_result.dishes[{dish_index}][{candidate_index}]"
    )
    matched_tags, matched_groups = validate_selected_candidate_tags(
        candidate["raw_candidate"],
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


def _find_selected_candidate(
    dishes: list[list[CandidateReference]],
    dish_index: int,
    recipe_name: str,
) -> tuple[int, CandidateReference]:
    """在最终菜品所属组内定位唯一候选。"""

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
    return matches[0]


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
                "sources": _build_tag_sources(
                    selected_index,
                    dish_index,
                    candidate_index,
                ),
                "text": _build_tag_text(recipe_name, group, tags),
            }
        )
    return reasons


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
    template = TAG_TEXT_TEMPLATES.get(group)
    if template is None:
        _internal(f"标签组缺少固定模板：{group}")
    return template.format(recipe_name=recipe_name, tags="、".join(tags))


def _build_filtering_reasons(
    dishes: list[list[CandidateReference]],
    planning: PlanningEvidence,
    decision: DecisionEvidence,
) -> list[FilteringReason]:
    selected = _collect_selected_evidence(dishes, planning)
    constraints = decision["effective_constraints"]
    reasons = _build_selected_tag_filtering_reasons(selected)
    reasons.extend(_build_dish_constraint_reasons(selected, constraints))
    names, indexes = _affected_values(selected)

    max_time = constraints["max_total_time_minutes"]
    if max_time is not None:
        reasons.append(
            _filtering_reason(
                "max_total_time_minutes",
                {"max_total_time_minutes": max_time},
                names,
                indexes,
                [_constraint_source("max_total_time_minutes")],
                f"本次菜谱总制作时间上限为{max_time}分钟。",
            )
        )
    max_difficulty = constraints["max_difficulty"]
    if max_difficulty is not None:
        reasons.append(
            _filtering_reason(
                "max_difficulty",
                {"max_difficulty": max_difficulty},
                names,
                indexes,
                [_constraint_source("max_difficulty")],
                f"本次菜谱难度上限为{max_difficulty}。",
            )
        )

    reasons.extend(_build_required_ingredient_reasons(selected, constraints))
    available = constraints["available_ingredients"]
    if available:
        reasons.append(
            _filtering_reason(
                "available_ingredients",
                {"available_ingredients": list(available)},
                names,
                indexes,
                [_constraint_source("available_ingredients")],
                "本次只保留核心食材均在可用食材"
                f"{'、'.join(available)}范围内的菜谱。",
            )
        )
    allergens = constraints["allergens"]
    if allergens:
        reasons.append(
            _filtering_reason(
                "allergen_exclusion",
                {"allergens": list(allergens)},
                names,
                indexes,
                [_constraint_source("allergens")],
                f"已按档案中的{'、'.join(allergens)}过敏信息，在候选筛选阶段"
                "排除含相关标准食材的菜谱。",
            )
        )
    reasons.append(
        _filtering_reason(
            "recommendability_gate",
            {"is_recommendable": True},
            names,
            indexes,
            [
                {
                    "component": "dish_filtering",
                    "paths": ["rules.is_recommendable"],
                }
            ],
            "本次只从允许推荐的菜谱中选择。",
        )
    )
    reasons.append(
        _filtering_reason(
            "candidate_stable_order",
            {"order": ["matched_tag_count_desc", "recipe_name_asc"]},
            names,
            indexes,
            [
                {
                    "component": "dish_filtering",
                    "paths": ["rules.candidate_order"],
                }
            ],
            "符合条件的候选先按标签命中数从多到少排列，"
            "命中数相同时按菜名稳定排序。",
        )
    )
    return reasons


def _collect_selected_evidence(
    dishes: list[list[CandidateReference]],
    planning: PlanningEvidence,
) -> list[dict[str, Any]]:
    selected_evidence: list[dict[str, Any]] = []
    for selected_index, selected in enumerate(planning["selected_dishes"]):
        dish_index = selected["dish_constraint_index"]
        recipe_name = selected["recipe_name"]
        candidate_index, candidate = _find_selected_candidate(
            dishes,
            dish_index,
            recipe_name,
        )
        location = (
            f"dish_filtering_result.dishes[{dish_index}]"
            f"[{candidate_index}]"
        )
        matched_tags, matched_groups = validate_selected_candidate_tags(
            candidate["raw_candidate"],
            location,
        )
        selected_evidence.append(
            {
                "selected_index": selected_index,
                "dish_index": dish_index,
                "candidate_index": candidate_index,
                "recipe_name": recipe_name,
                "matched_tags": matched_tags,
                "matched_groups": matched_groups,
            }
        )
    return selected_evidence


def _build_selected_tag_filtering_reasons(
    selected: list[dict[str, Any]],
) -> list[FilteringReason]:
    reasons: list[FilteringReason] = []
    for group in TAG_GROUPS:
        grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for item in selected:
            tags = tuple(
                tag
                for tag in item["matched_tags"]
                if TAG_TO_GROUP.get(tag) == group
            )
            if tags:
                grouped.setdefault(tags, []).append(item)
        for tags, affected in grouped.items():
            names, indexes = _affected_values(affected)
            sources: list[ReasonSource] = []
            for item in affected:
                sources.extend(
                    _build_tag_sources(
                        item["selected_index"],
                        item["dish_index"],
                        item["candidate_index"],
                    )
                )
            reasons.append(
                _filtering_reason(
                    "selected_tag_match",
                    {"matched_group": group, "matched_tags": list(tags)},
                    names,
                    indexes,
                    sources,
                    _build_tag_text("、".join(names), group, list(tags)),
                )
            )
    return reasons


def _build_dish_constraint_reasons(
    selected: list[dict[str, Any]],
    constraints: dict[str, Any],
) -> list[FilteringReason]:
    reasons: list[FilteringReason] = []
    negative_groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    type_groups: dict[str, list[dict[str, Any]]] = {}
    for item in selected:
        dish_index = item["dish_index"]
        dish = constraints["dishes"][dish_index]
        negative = tuple(
            tag
            for key, tag in TASTE_KEY_TO_TAG.items()
            if dish["taste_preferences"].get(key) is False
        )
        if negative:
            negative_groups.setdefault(negative, []).append(item)
        dish_type = dish["dish_type"]
        if dish_type != "未指定":
            type_groups.setdefault(dish_type, []).append(item)

    for tags, affected in negative_groups.items():
        names, indexes = _affected_values(affected)
        reasons.append(
            _filtering_reason(
                "negative_taste",
                {"excluded_taste_tags": list(tags)},
                names,
                indexes,
                [
                    _constraint_source(
                        f"dishes[{item['dish_index']}].taste_preferences"
                    )
                    for item in affected
                ],
                f"本次排除命中{'、'.join(tags)}口味标签的候选菜谱。",
            )
        )
    for dish_type, affected in type_groups.items():
        names, indexes = _affected_values(affected)
        reasons.append(
            _filtering_reason(
                "dish_type",
                {"dish_type": dish_type},
                names,
                indexes,
                [
                    _constraint_source(
                        f"dishes[{item['dish_index']}].dish_type"
                    )
                    for item in affected
                ],
                f"本次候选菜谱限定为{dish_type}。",
            )
        )
    return reasons


def _build_required_ingredient_reasons(
    selected: list[dict[str, Any]],
    constraints: dict[str, Any],
) -> list[FilteringReason]:
    grouped: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    for item in selected:
        groups = constraints["dishes"][item["dish_index"]][
            "required_ingredient_groups"
        ]
        if not groups:
            continue
        key = json.dumps(groups, ensure_ascii=False, sort_keys=True)
        if key not in grouped:
            grouped[key] = (copy.deepcopy(groups), [])
        grouped[key][1].append(item)

    reasons: list[FilteringReason] = []
    for groups, affected in grouped.values():
        names, indexes = _affected_values(affected)
        clauses = []
        for group in groups:
            label = "全部包含" if group["match"] == "all" else "任一包含"
            clauses.append(
                label + "、".join(item["value"] for item in group["items"])
            )
        reasons.append(
            _filtering_reason(
                "required_ingredient_groups",
                {"required_ingredient_groups": groups},
                names,
                indexes,
                [
                    _constraint_source(
                        f"dishes[{item['dish_index']}]"
                        ".required_ingredient_groups"
                    )
                    for item in affected
                ],
                "本次必需食材组需同时满足：" + "；".join(clauses) + "。",
            )
        )
    return reasons


def _build_planning_reasons(
    planning: PlanningEvidence,
    decision: DecisionEvidence,
) -> list[PlanningReason]:
    constraints = decision["effective_constraints"]
    names = [item["recipe_name"] for item in planning["selected_dishes"]]
    indexes = list(
        dict.fromkeys(
            item["dish_constraint_index"]
            for item in planning["selected_dishes"]
        )
    )
    reasons = [
        _planning_reason(
            "dish_count",
            {
                "diner_count": planning["diner_count"],
                "total_dish_count": constraints["total_dish_count"],
                "dish_counts": [dish["count"] for dish in constraints["dishes"]],
            },
            names,
            indexes,
            [_constraint_source("diner_count"), _constraint_source("dishes")],
            _build_dish_count_text(planning, constraints),
        ),
        _planning_reason(
            "unique_recipe_and_fixed_nutrition",
            {"unique_recipe_names": True, "fixed_recipe_nutrition": True},
            names,
            indexes,
            [{"component": "menu_planning", "paths": ["rules.fixed_recipe"]}],
            "同名菜不重复；菜谱配方和整份营养按库中固定值计算，"
            "本次不调整食材克重。",
        ),
        _planning_reason(
            "candidate_stage",
            {
                "candidate_attempts": copy.deepcopy(decision["candidate_attempts"]),
                "nutrition_score": planning["nutrition_score"],
            },
            names,
            indexes,
            [
                {
                    "component": "menu_recommendation",
                    "paths": ["candidate_attempts"],
                }
            ],
            _build_candidate_stage_text(decision["candidate_attempts"]),
        ),
        _planning_reason(
            "selection_priority",
            {
                "priority": [
                    "nutrition_score_desc",
                    "abnormal_nutrient_count_asc",
                    "matched_tag_count_desc",
                    "candidate_order_asc",
                ]
            },
            names,
            indexes,
            [{"component": "menu_planning", "paths": ["rules.objective"]}],
            "在满足约束的菜单中，依次按营养得分高、正常区间外营养项少、"
            "标签命中多、候选顺序靠前进行选择。",
        ),
        _planning_reason(
            "proven_optimal",
            {"is_proven_optimal": True},
            names,
            indexes,
            [{"component": "menu_planning", "paths": ["solver.status"]}],
            "本次返回的是在上述规则下已证明最优的菜单。",
        ),
    ]
    return reasons


def _build_dish_count_text(
    planning: PlanningEvidence,
    constraints: dict[str, Any],
) -> str:
    total = constraints["total_dish_count"]
    if total is not None:
        return f"本次按明确指定的总菜数选择{total}道菜。"
    clauses = []
    for index, dish in enumerate(constraints["dishes"], start=1):
        count = dish["count"]
        clauses.append(
            f"第{index}组明确选择{count}道"
            if count is not None
            else f"第{index}组至少选择一道"
        )
    if any(dish["count"] is not None for dish in constraints["dishes"]):
        return "；".join(clauses) + "，各组规则需同时满足。"
    return (
        f"{planning['diner_count']}人且未指定菜品总数，"
        f"本次按默认数量规则选择{len(planning['selected_dishes'])}道菜。"
    )


def _build_candidate_stage_text(
    attempts: list[CandidateAttemptEvidence],
) -> str:
    final = attempts[-1]
    if final["candidate_limit"] is None and final["nutrition_score"] < 8:
        return "本次使用全量候选得到可行菜单，营养得分未达到8分目标。"
    if len(attempts) == 1:
        return "本次在优先候选范围内找到达到营养目标的可行菜单。"
    return "本次扩大候选范围后找到可行菜单。"


def _affected_values(
    selected: list[dict[str, Any]],
) -> tuple[list[str], list[int]]:
    return (
        [item["recipe_name"] for item in selected],
        list(dict.fromkeys(item["dish_index"] for item in selected)),
    )


def _constraint_source(path: str) -> ReasonSource:
    return {"component": "constraint_integration", "paths": [path]}


def _filtering_reason(
    rule: FilteringRule,
    details: dict[str, Any],
    names: list[str],
    indexes: list[int],
    sources: list[ReasonSource],
    text: str,
) -> FilteringReason:
    return {
        "reason_type": "filtering_rule",
        "rule": rule,
        "details": details,
        "affected_recipe_names": names,
        "dish_constraint_indexes": indexes,
        "sources": sources,
        "text": text,
    }


def _planning_reason(
    rule: PlanningRule,
    details: dict[str, Any],
    names: list[str],
    indexes: list[int],
    sources: list[ReasonSource],
    text: str,
) -> PlanningReason:
    return {
        "reason_type": "planning_rule",
        "rule": rule,
        "details": details,
        "affected_recipe_names": names,
        "dish_constraint_indexes": indexes,
        "sources": sources,
        "text": text,
    }


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
