from __future__ import annotations

from typing import Any, Literal, TypedDict


CONFIRMATION_QUESTION = "请确认这次要安排早餐、午餐还是晚餐？"


class PlanningContext(TypedDict):
    """进入规划前已经生效的三个关键维度。"""

    meal_period: str | None
    meal_period_source: Literal["explicit", "current_time"] | None
    diner_count: int
    diner_count_source: Literal["explicit", "default"]
    total_dish_count: int
    total_dish_count_source: Literal[
        "explicit",
        "dish_counts",
        "default",
    ]


class KnownConstraint(TypedDict):
    """一条可直接展示的已知约束。"""

    path: str
    label: str
    value: str
    source: Literal["explicit", "current_time", "default", "derived"]


class Confirmation(TypedDict):
    """需要用户确认餐次时返回的固定问题。"""

    reason: str
    options: list[str]
    question: str


class ConfirmationState(TypedDict):
    """约束确认服务的公共状态。"""

    status: Literal[
        "in_progress",
        "needs_confirmation",
        "ready_for_planning",
    ]
    merged_constraints: dict[str, Any] | None
    planning_context: PlanningContext | None
    known_constraints: list[KnownConstraint]
    confirmation: Confirmation | None
    message: str | None


class ConstraintConfirmationError(Exception):
    """约束确认流程的可预期接口错误。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


__all__ = [
    "CONFIRMATION_QUESTION",
    "Confirmation",
    "ConfirmationState",
    "ConstraintConfirmationError",
    "KnownConstraint",
    "PlanningContext",
]
