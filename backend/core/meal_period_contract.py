from __future__ import annotations

from datetime import datetime
from typing import TypedDict

from backend.core.nutrition_contract import MEAL_PERIODS


# 合法输入的餐次：三个正餐可直接解析，下午茶合法但需要与用户确认
KNOWN_MEAL_PERIODS = ("早餐", "午餐", "晚餐", "下午茶")

# 各正餐的时间窗口（分钟制，含端点）：早餐 05:00~10:00，午餐 11:00~14:00，晚餐 17:00~21:00
MEAL_WINDOWS_MINUTES = {
    "早餐": (5 * 60, 10 * 60),
    "午餐": (11 * 60, 14 * 60),
    "晚餐": (17 * 60, 21 * 60),
}

# 待确认时提供给调用方的固定选项
CONFIRM_OPTIONS = ["早餐", "午餐", "晚餐"]


class MealPeriodResolution(TypedDict):
    """餐次解析结果。"""

    status: str
    meal_period: str | None
    source: str
    reason: str | None
    options: list[str]


class MealPeriodResolutionError(Exception):
    """餐次解析的可预期接口错误。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class MealPeriodResolutionValidationError(MealPeriodResolutionError):
    """输入不合法（400）。"""


def validate_meal_periods(meal_periods: object) -> list[str]:
    """校验输入餐次数组，返回原列表；不合法时抛 400。"""

    if type(meal_periods) is not list:
        raise MealPeriodResolutionValidationError(
            400, "输入必须是餐次数组"
        )
    for meal_period in meal_periods:
        if type(meal_period) is not str:
            raise MealPeriodResolutionValidationError(
                400, "餐次必须是字符串"
            )
        if meal_period not in KNOWN_MEAL_PERIODS:
            raise MealPeriodResolutionValidationError(
                400, f"未知餐次：{meal_period}"
            )
    if len(set(meal_periods)) != len(meal_periods):
        raise MealPeriodResolutionValidationError(400, "餐次存在重复值")
    return meal_periods


def meal_window_for(current: datetime) -> str | None:
    """按业务时区本地时间匹配正餐窗口；命中返回餐次，未命中返回 None。

    时间比较精确到分钟并包含端点，秒和微秒不参与判断。
    """

    minutes = current.hour * 60 + current.minute
    for meal_period in MEAL_PERIODS:
        lower, upper = MEAL_WINDOWS_MINUTES[meal_period]
        if lower <= minutes <= upper:
            return meal_period
    return None


__all__ = [
    "CONFIRM_OPTIONS",
    "KNOWN_MEAL_PERIODS",
    "MEAL_WINDOWS_MINUTES",
    "MealPeriodResolution",
    "MealPeriodResolutionError",
    "MealPeriodResolutionValidationError",
    "meal_window_for",
    "validate_meal_periods",
]
