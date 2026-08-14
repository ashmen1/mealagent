from __future__ import annotations

import importlib
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest


SPEC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPEC_DIR.parents[1]
repo_root_text = str(REPO_ROOT)
if repo_root_text not in sys.path:
    sys.path.insert(0, repo_root_text)


from backend.core.meal_period_contract import CONFIRM_OPTIONS


# 解析结果的完整字段集合，用于验证不夹带确认文案等额外内容
RESOLUTION_KEYS = {"status", "meal_period", "source", "reason", "options"}


@pytest.fixture
def production_contract():
    try:
        module = importlib.import_module(
            "backend.services.meal_period_resolution"
        )
        service = module.MealPeriodResolutionService
        resolution_error = module.MealPeriodResolutionError
        validation_error = module.MealPeriodResolutionValidationError
    except (ModuleNotFoundError, AttributeError) as exc:
        pytest.fail(
            "缺少 Spec_07 约定的生产接口："
            "backend.services.meal_period_resolution."
            "MealPeriodResolutionService、MealPeriodResolutionError 或 "
            "MealPeriodResolutionValidationError；"
            f"原始错误：{exc}",
            pytrace=False,
        )

    return SimpleNamespace(
        MealPeriodResolutionService=service,
        MealPeriodResolutionError=resolution_error,
        MealPeriodResolutionValidationError=validation_error,
    )


@pytest.fixture
def build_service(production_contract):
    def build(
        clock: Callable[[], datetime],
        **kwargs: Any,
    ):
        return production_contract.MealPeriodResolutionService(
            clock=clock,
            **kwargs,
        )

    return build


@pytest.fixture
def clock_at() -> Callable[..., Callable[[], datetime]]:
    """构造固定时钟：返回指定的上海本地时间，用于验证时间窗口判断"""

    def clock_at_impl(
        hour: int,
        minute: int = 0,
        second: int = 0,
        microsecond: int = 0,
    ) -> Callable[[], datetime]:
        def clock() -> datetime:
            return datetime(2026, 8, 14, hour, minute, second, microsecond)

        return clock

    return clock_at_impl


@pytest.fixture
def failing_clock() -> Callable[[], datetime]:
    """时钟读取抛异常的时钟，用于验证 500 分支"""

    def clock() -> datetime:
        raise RuntimeError("时钟故障")

    return clock


@pytest.fixture
def invalid_clock() -> Callable[[], object]:
    """返回非法时间值的时钟，用于验证 500 分支"""

    def clock() -> object:
        return "非法时间"

    return clock
