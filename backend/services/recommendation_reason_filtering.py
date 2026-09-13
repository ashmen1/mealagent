from __future__ import annotations

import copy
import json
from typing import Any

from backend.core.dish_filtering_contract import (
    TAG_GROUPS,
    TAG_TO_GROUP,
    TASTE_KEY_TO_TAG,
)
from backend.core.recommendation_reason_contract import (
    FilteringReason,
    FilteringRule,
    ReasonSource,
)
from backend.core.recommendation_reason_validation import DecisionEvidence
from backend.core.text_formatting import join_chinese_items
from backend.services.recommendation_reason_evidence import (
    SelectedCandidateEvidence,
    affected_values,
    build_tag_sources,
    build_tag_text,
    constraint_source,
)


def build_filtering_reasons(
    selected: list[SelectedCandidateEvidence],
    decision: DecisionEvidence,
) -> list[FilteringReason]:
    """按固定顺序生成本次真正执行的筛选依据。"""

    constraints = decision["effective_constraints"]
    reasons = _build_selected_tag_reasons(selected)
    reasons.extend(_build_dish_constraint_reasons(selected, constraints))
    names, indexes = affected_values(selected)

    max_time = constraints["max_total_time_minutes"]
    if max_time is not None:
        reasons.append(
            _reason(
                "max_total_time_minutes",
                {"max_total_time_minutes": max_time},
                names,
                indexes,
                [constraint_source("max_total_time_minutes")],
                f"本次菜谱总制作时间上限为{max_time}分钟。",
            )
        )
    max_difficulty = constraints["max_difficulty"]
    if max_difficulty is not None:
        reasons.append(
            _reason(
                "max_difficulty",
                {"max_difficulty": max_difficulty},
                names,
                indexes,
                [constraint_source("max_difficulty")],
                f"本次菜谱难度上限为{max_difficulty}。",
            )
        )

    reasons.extend(_build_required_ingredient_reasons(selected, constraints))
    reasons.extend(_build_staple_ingredient_reasons(selected, constraints))
    available = constraints["available_ingredients"]
    if available:
        reasons.append(
            _reason(
                "available_ingredients",
                {"available_ingredients": list(available)},
                names,
                indexes,
                [constraint_source("available_ingredients")],
                "本次只保留核心食材均在可用食材"
                f"{'、'.join(available)}范围内的菜谱。",
            )
        )
    allergens = constraints["allergens"]
    if allergens:
        reasons.append(
            _reason(
                "allergen_exclusion",
                {"allergens": list(allergens)},
                names,
                indexes,
                [constraint_source("allergens")],
                f"已按档案中的{'、'.join(allergens)}过敏信息，在候选筛选阶段"
                "排除含相关标准食材的菜谱。",
            )
        )
    reasons.extend(_build_permanent_reasons(names, indexes))
    return reasons


def _build_selected_tag_reasons(
    selected: list[SelectedCandidateEvidence],
) -> list[FilteringReason]:
    reasons: list[FilteringReason] = []
    for group in TAG_GROUPS:
        grouped: dict[
            tuple[str, ...],
            list[SelectedCandidateEvidence],
        ] = {}
        for item in selected:
            tags = tuple(
                tag
                for tag in item["matched_tags"]
                if TAG_TO_GROUP.get(tag) == group
            )
            if tags:
                grouped.setdefault(tags, []).append(item)
        for tags, affected in grouped.items():
            names, indexes = affected_values(affected)
            sources = [
                source
                for item in affected
                for source in build_tag_sources(item)
            ]
            reasons.append(
                _reason(
                    "selected_tag_match",
                    {"matched_group": group, "matched_tags": list(tags)},
                    names,
                    indexes,
                    sources,
                    build_tag_text(names, group, list(tags)),
                )
            )
    return reasons


def _build_dish_constraint_reasons(
    selected: list[SelectedCandidateEvidence],
    constraints: dict[str, Any],
) -> list[FilteringReason]:
    negative_groups: dict[
        tuple[str, ...],
        list[SelectedCandidateEvidence],
    ] = {}
    type_groups: dict[str, list[SelectedCandidateEvidence]] = {}
    dishes = constraints["dishes"]
    for item in selected:
        dish = dishes[item["dish_index"]]
        taste_preferences = dish["taste_preferences"]
        negative = tuple(
            tag
            for key, tag in TASTE_KEY_TO_TAG.items()
            if taste_preferences.get(key) is False
        )
        if negative:
            negative_groups.setdefault(negative, []).append(item)
        dish_type = dish["dish_type"]
        if dish_type != "未指定":
            type_groups.setdefault(dish_type, []).append(item)

    reasons: list[FilteringReason] = []
    for tags, affected in negative_groups.items():
        names, indexes = affected_values(affected)
        reasons.append(
            _reason(
                "negative_taste",
                {"excluded_taste_tags": list(tags)},
                names,
                indexes,
                [
                    constraint_source(
                        f"dishes[{item['dish_index']}].taste_preferences"
                    )
                    for item in affected
                ],
                f"本次排除命中{'、'.join(tags)}口味标签的候选菜谱。",
            )
        )
    for dish_type, affected in type_groups.items():
        names, indexes = affected_values(affected)
        reasons.append(
            _reason(
                "dish_type",
                {"dish_type": dish_type},
                names,
                indexes,
                [
                    constraint_source(
                        f"dishes[{item['dish_index']}].dish_type"
                    )
                    for item in affected
                ],
                f"本次候选菜谱限定为{dish_type}。",
            )
        )
    return reasons


def _build_required_ingredient_reasons(
    selected: list[SelectedCandidateEvidence],
    constraints: dict[str, Any],
) -> list[FilteringReason]:
    grouped: dict[
        str,
        tuple[list[dict[str, Any]], list[SelectedCandidateEvidence]],
    ] = {}
    dishes = constraints["dishes"]
    for item in selected:
        dish = dishes[item["dish_index"]]
        groups = dish["required_ingredient_groups"]
        if not groups:
            continue
        key = json.dumps(groups, ensure_ascii=False, sort_keys=True)
        if key not in grouped:
            grouped[key] = (copy.deepcopy(groups), [])
        grouped[key][1].append(item)

    reasons: list[FilteringReason] = []
    for groups, affected in grouped.values():
        names, indexes = affected_values(affected)
        clauses = []
        for group in groups:
            match = group["match"]
            items = group["items"]
            label = "全部包含" if match == "all" else "任一包含"
            clauses.append(
                label
                + "、".join(item["value"] for item in items)
            )
        reasons.append(
            _reason(
                "required_ingredient_groups",
                {"required_ingredient_groups": groups},
                names,
                indexes,
                [
                    constraint_source(
                        f"dishes[{item['dish_index']}]"
                        ".required_ingredient_groups"
                    )
                    for item in affected
                ],
                "本次必需食材组需同时满足：" + "；".join(clauses) + "。",
            )
        )
    return reasons


def _build_staple_ingredient_reasons(
    selected: list[SelectedCandidateEvidence],
    constraints: dict[str, Any],
) -> list[FilteringReason]:
    reasons: list[FilteringReason] = []
    required_groups: dict[
        str,
        tuple[dict[str, Any], list[SelectedCandidateEvidence]],
    ] = {}
    excluded_groups: dict[
        tuple[str, ...],
        list[SelectedCandidateEvidence],
    ] = {}
    dishes = constraints["dishes"]
    for item in selected:
        dish = dishes[item["dish_index"]]
        required = dish["required_staple_ingredients"]
        if required is not None:
            key = json.dumps(required, ensure_ascii=False, sort_keys=True)
            if key not in required_groups:
                required_groups[key] = (copy.deepcopy(required), [])
            required_groups[key][1].append(item)
        excluded = tuple(dish["excluded_staple_ingredients"])
        if excluded:
            excluded_groups.setdefault(excluded, []).append(item)

    for required, affected in required_groups.values():
        names, indexes = affected_values(affected)
        items = list(required["items"])
        if required["match"] == "all" and len(items) > 1:
            text = (
                "本次主食来源需同时包含"
                f"{join_chinese_items(items, '和')}。"
            )
        else:
            conjunction = "或" if required["match"] == "any" else "和"
            text = (
                "本次主食来源限定为"
                f"{join_chinese_items(items, conjunction)}。"
            )
        reasons.append(
            _reason(
                "required_staple_ingredients",
                {"required_staple_ingredients": required},
                names,
                indexes,
                [
                    constraint_source(
                        f"dishes[{item['dish_index']}]"
                        ".required_staple_ingredients"
                    )
                    for item in affected
                ],
                text,
            )
        )

    for excluded, affected in excluded_groups.items():
        names, indexes = affected_values(affected)
        values = list(excluded)
        reasons.append(
            _reason(
                "excluded_staple_ingredients",
                {"excluded_staple_ingredients": values},
                names,
                indexes,
                [
                    constraint_source(
                        f"dishes[{item['dish_index']}]"
                        ".excluded_staple_ingredients"
                    )
                    for item in affected
                ],
                f"本次排除以{'、'.join(values)}作为主食来源的菜谱。",
            )
        )
    return reasons


def _build_permanent_reasons(
    names: list[str],
    indexes: list[int],
) -> list[FilteringReason]:
    return [
        _reason(
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
        ),
        _reason(
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
        ),
    ]


def _reason(
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


__all__ = ["build_filtering_reasons"]
