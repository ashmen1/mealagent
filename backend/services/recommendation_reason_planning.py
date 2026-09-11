from __future__ import annotations

import copy
from typing import Any

from backend.core.recommendation_reason_contract import (
    PlanningReason,
    PlanningRule,
    ReasonSource,
)
from backend.core.recommendation_reason_validation import (
    CandidateAttemptEvidence,
    DecisionEvidence,
    PlanningEvidence,
)
from backend.services.recommendation_reason_evidence import (
    SelectedCandidateEvidence,
    affected_values,
    constraint_source,
)


def build_planning_reasons(
    selected: list[SelectedCandidateEvidence],
    planning: PlanningEvidence,
    decision: DecisionEvidence,
) -> list[PlanningReason]:
    """按固定顺序生成数量、候选阶段和选优依据。"""

    constraints = decision["effective_constraints"]
    names, indexes = affected_values(selected)
    return [
        _reason(
            "dish_count",
            {
                "diner_count": planning["diner_count"],
                "total_dish_count": constraints["total_dish_count"],
                "dish_counts": [
                    dish["count"] for dish in constraints["dishes"]
                ],
            },
            names,
            indexes,
            [
                constraint_source("diner_count"),
                constraint_source("dishes"),
            ],
            _build_dish_count_text(planning, constraints),
        ),
        _reason(
            "unique_recipe_and_fixed_nutrition",
            {"unique_recipe_names": True, "fixed_recipe_nutrition": True},
            names,
            indexes,
            [
                {
                    "component": "menu_planning",
                    "paths": ["rules.fixed_recipe"],
                }
            ],
            "同名菜不重复；菜谱配方和整份营养按库中固定值计算，"
            "本次不调整食材克重。",
        ),
        _reason(
            "candidate_stage",
            {
                "candidate_attempts": copy.deepcopy(
                    decision["candidate_attempts"]
                ),
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
        _reason(
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
            [
                {
                    "component": "menu_planning",
                    "paths": ["rules.objective"],
                }
            ],
            "在满足约束的菜单中，依次按营养得分高、正常区间外营养项少、"
            "标签命中多、候选顺序靠前进行选择。",
        ),
        _reason(
            "proven_optimal",
            {"is_proven_optimal": True},
            names,
            indexes,
            [
                {
                    "component": "menu_planning",
                    "paths": ["solver.status"],
                }
            ],
            "本次返回的是在上述规则下已证明最优的菜单。",
        ),
    ]


def _build_dish_count_text(
    planning: PlanningEvidence,
    constraints: dict[str, Any],
) -> str:
    total = constraints["total_dish_count"]
    if total is not None:
        return f"本次按明确指定的总菜数选择{total}道菜。"
    dishes = constraints["dishes"]
    clauses = []
    for index, dish in enumerate(dishes, start=1):
        count = dish["count"]
        clauses.append(
            f"第{index}组明确选择{count}道"
            if count is not None
            else f"第{index}组至少选择一道"
        )
    if any(dish["count"] is not None for dish in dishes):
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


def _reason(
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


__all__ = ["build_planning_reasons"]
