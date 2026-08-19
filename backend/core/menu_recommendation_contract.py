from __future__ import annotations

from typing import Any, Literal, TypedDict

from backend.core.dish_filtering_contract import DishFilteringResult
from backend.core.menu_planning_contract import MenuPlanningResult
from backend.core.recommendation_reason_contract import (
    RecommendationReasonResult,
)


class CandidateAttempt(TypedDict):
    """一次候选规模下的规划结果。"""

    candidate_limit: int | None
    candidate_counts: list[int]
    outcome: Literal["infeasible", "below_target", "accepted"]
    nutrition_score: int | None


class QualityWarning(TypedDict):
    """最终菜单仍可返回的结构化质量提醒。"""

    code: Literal["nutrition_score_below_target"]
    nutrition_score: int
    target_score: int


class MenuGenerationResult(TypedDict):
    """统一推荐入口的固定结果结构。"""

    session_id: int
    profile_id: int
    dialogue_id: int
    status: Literal[
        "in_progress",
        "needs_confirmation",
        "constraint_conflict",
        "unmatched_allergen",
        "empty_candidate",
        "planning_infeasible",
        "recommended",
    ]
    confirmation_state: dict[str, Any]
    conflicts: list[dict[str, Any]]
    unmatched_allergens: list[str]
    empty_dish_indexes: list[int]
    dish_filtering_result: DishFilteringResult | None
    candidate_attempts: list[CandidateAttempt]
    menu_planning_result: MenuPlanningResult | None
    recommendation_reason_result: RecommendationReasonResult | None
    quality_warnings: list[QualityWarning]


class MenuRecommendationError(Exception):
    """统一推荐链路的接口错误。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


__all__ = [
    "CandidateAttempt",
    "MenuGenerationResult",
    "MenuRecommendationError",
    "QualityWarning",
]
