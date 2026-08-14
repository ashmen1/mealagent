from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo

from backend.core.meal_period_contract import (
    CONFIRM_OPTIONS,
    MEAL_PERIODS,
    MealPeriodResolution,
    MealPeriodResolutionError,
    MealPeriodResolutionValidationError,
    meal_window_for,
    validate_meal_periods,
)


Clock = Callable[[], datetime]


def _resolved(meal_period: str, source: str) -> MealPeriodResolution:
    """构造已解析结果。"""

    return {
        "status": "resolved",
        "meal_period": meal_period,
        "source": source,
        "reason": None,
        "options": [],
    }


def _needs_confirmation(
    source: str,
    reason: str,
) -> MealPeriodResolution:
    """构造待确认结果，选项固定为三个正餐。"""

    return {
        "status": "needs_confirmation",
        "meal_period": None,
        "source": source,
        "reason": reason,
        "options": CONFIRM_OPTIONS,
    }


class MealPeriodResolutionService:
    """解析用户餐次：明确餐次优先，未明确时按业务时区当前时间判断。"""

    def __init__(
        self,
        clock: Clock,
        timezone_name: str = "Asia/Shanghai",
    ) -> None:
        if not callable(clock):
            raise MealPeriodResolutionError(500, "时钟无效")
        self._clock = clock
        try:
            self._timezone: tzinfo = ZoneInfo(timezone_name)
        except Exception as exc:
            raise MealPeriodResolutionError(
                500, f"业务时区无效：{timezone_name}"
            ) from exc

    def resolve(self, meal_periods: object) -> MealPeriodResolution:
        """按输入餐次数组返回解析结果；输入不合法抛 400，时钟失败抛 500。"""

        validated = validate_meal_periods(meal_periods)
        if validated:
            return self._resolve_explicit(validated)
        return self._resolve_by_current_time()

    def _resolve_explicit(
        self,
        meal_periods: list[str],
    ) -> MealPeriodResolution:
        """用户明确给出餐次时的解析路径，不读取时钟。"""

        if len(meal_periods) == 1:
            meal_period = meal_periods[0]
            if meal_period in MEAL_PERIODS:
                return _resolved(meal_period, "explicit")
            return _needs_confirmation(
                "explicit", "unsupported_meal_period"
            )
        return _needs_confirmation("explicit", "multiple_meal_periods")

    def _resolve_by_current_time(self) -> MealPeriodResolution:
        """用户未给出餐次时按业务时区当前时间判断，失败抛 500 不兜底。"""

        try:
            current = self._clock()
            if not isinstance(current, datetime):
                raise MealPeriodResolutionError(500, "时钟结果不是时间")
            if current.tzinfo is not None:
                current = current.astimezone(self._timezone)
            meal_period = meal_window_for(current)
        except MealPeriodResolutionError:
            raise
        except Exception as exc:
            raise MealPeriodResolutionError(500, "时钟读取失败") from exc

        if meal_period is None:
            return _needs_confirmation(
                "current_time", "outside_meal_window"
            )
        return _resolved(meal_period, "current_time")


__all__ = [
    "MealPeriodResolutionError",
    "MealPeriodResolutionService",
    "MealPeriodResolutionValidationError",
]
