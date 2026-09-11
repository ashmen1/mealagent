from __future__ import annotations

from typing import Final, NoReturn, TypedDict

from backend.core.recommendation_reason_contract import (
    ReasonSource,
    RecommendationReasonError,
)
from backend.core.recommendation_reason_validation import (
    CandidateReference,
    PlanningEvidence,
    validate_selected_candidate_tags,
)


TAG_TEXT_TEMPLATES: Final[dict[str, str]] = {
    "餐次": "{recipe_name}适合本次{tags}。",
    "口味": "{recipe_name}符合本次{tags}口味偏好。",
    "菜系": "{recipe_name}符合本次{tags}偏好。",
    "功效": "{recipe_name}匹配本次提出的{tags}功效标签。",
    "人群": "{recipe_name}匹配本次提出的{tags}人群标签。",
}


class SelectedCandidateEvidence(TypedDict):
    """最终菜品在筛选结果中的可追溯证据。"""

    selected_index: int
    dish_index: int
    candidate_index: int
    recipe_name: str
    matched_tags: list[str]
    matched_groups: list[str]


def collect_selected_evidence(
    dishes: list[list[CandidateReference]],
    planning: PlanningEvidence,
) -> list[SelectedCandidateEvidence]:
    """一次定位全部最终菜品，供各类理由共享同一份证据。"""

    result: list[SelectedCandidateEvidence] = []
    for selected_index, selected in enumerate(planning["selected_dishes"]):
        dish_index = selected["dish_constraint_index"]
        recipe_name = selected["recipe_name"]
        candidate_index, candidate = find_selected_candidate(
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
        result.append(
            {
                "selected_index": selected_index,
                "dish_index": dish_index,
                "candidate_index": candidate_index,
                "recipe_name": recipe_name,
                "matched_tags": matched_tags,
                "matched_groups": matched_groups,
            }
        )
    return result


def find_selected_candidate(
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


def build_tag_sources(evidence: SelectedCandidateEvidence) -> list[ReasonSource]:
    return [
        {
            "component": "menu_planning",
            "paths": [
                f"selected_dishes[{evidence['selected_index']}].dish_constraint_index",
                f"selected_dishes[{evidence['selected_index']}].recipe_name",
            ],
        },
        {
            "component": "dish_filtering",
            "paths": [
                f"dishes[{evidence['dish_index']}]"
                f"[{evidence['candidate_index']}].matched_tags",
                f"dishes[{evidence['dish_index']}]"
                f"[{evidence['candidate_index']}].matched_groups",
            ],
        },
    ]


def build_tag_text(recipe_names: list[str], group: str, tags: list[str]) -> str:
    template = TAG_TEXT_TEMPLATES.get(group)
    if template is None:
        _internal(f"标签组缺少固定模板：{group}")
    return template.format(
        recipe_name="、".join(recipe_names),
        tags="、".join(tags),
    )


def affected_values(
    selected: list[SelectedCandidateEvidence],
) -> tuple[list[str], list[int]]:
    return (
        [item["recipe_name"] for item in selected],
        list(dict.fromkeys(item["dish_index"] for item in selected)),
    )


def constraint_source(path: str) -> ReasonSource:
    """创建一条指向生效约束的来源记录。"""

    return {"component": "constraint_integration", "paths": [path]}


def _internal(message: str) -> NoReturn:
    raise RecommendationReasonError(500, message)


__all__ = [
    "SelectedCandidateEvidence",
    "affected_values",
    "build_tag_sources",
    "build_tag_text",
    "collect_selected_evidence",
    "constraint_source",
    "find_selected_candidate",
]
