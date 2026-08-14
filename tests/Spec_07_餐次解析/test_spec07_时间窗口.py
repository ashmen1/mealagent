from __future__ import annotations

import pytest

from .spec07_support import CONFIRM_OPTIONS


# 空数组时按上海本地时间判断，窗口端点包含，秒和微秒不参与判断

LOWER_BOUNDS = [
    (5, 0, "早餐"),
    (11, 0, "午餐"),
    (17, 0, "晚餐"),
]


@pytest.mark.parametrize(("hour", "minute", "expected"), LOWER_BOUNDS)
def test_窗口下界端点包含(
    production_contract, build_service, clock_at, hour, minute, expected
):
    service = build_service(clock_at(hour, minute))

    result = service.resolve([])

    assert result["status"] == "resolved"
    assert result["meal_period"] == expected
    assert result["source"] == "current_time"


UPPER_BOUNDS = [
    (10, 0, "早餐"),
    (14, 0, "午餐"),
    (21, 0, "晚餐"),
]


@pytest.mark.parametrize(("hour", "minute", "expected"), UPPER_BOUNDS)
def test_窗口上界端点包含(
    production_contract, build_service, clock_at, hour, minute, expected
):
    service = build_service(clock_at(hour, minute))

    result = service.resolve([])

    assert result["status"] == "resolved"
    assert result["meal_period"] == expected
    assert result["source"] == "current_time"


# 秒和微秒不参与判断：59 秒仍属于该分钟

SECONDS_IGNORED = [
    (9, 59, 59, 0, "早餐"),      # 09:59:59 在早餐窗口内
    (10, 0, 59, 0, "早餐"),      # 10:00:59 截到 10:00，仍为早餐端点
    (10, 0, 0, 999999, "早餐"),  # 微秒不参与判断
    (21, 0, 59, 0, "晚餐"),      # 21:00:59 截到 21:00，仍为晚餐端点
]


@pytest.mark.parametrize(
    ("hour", "minute", "second", "microsecond", "expected"),
    SECONDS_IGNORED,
)
def test_秒和微秒不参与时间判断(
    production_contract,
    build_service,
    clock_at,
    hour,
    minute,
    second,
    microsecond,
    expected,
):
    service = build_service(clock_at(hour, minute, second, microsecond))

    result = service.resolve([])

    assert result["status"] == "resolved"
    assert result["meal_period"] == expected


OUTSIDE_WINDOWS = [
    (0, 0),    # 午夜
    (4, 59),   # 早餐窗口前 1 分钟
    (10, 1),   # 早餐窗口后 1 分钟
    (14, 1),   # 午餐窗口后 1 分钟
    (16, 59),  # 午餐与晚餐之间
    (21, 1),   # 晚餐窗口后 1 分钟
    (23, 30),  # 深夜
]


@pytest.mark.parametrize(("hour", "minute"), OUTSIDE_WINDOWS)
def test_空数组且不在饭点返回待确认(
    production_contract, build_service, clock_at, hour, minute
):
    service = build_service(clock_at(hour, minute))

    result = service.resolve([])

    assert result == {
        "status": "needs_confirmation",
        "meal_period": None,
        "source": "current_time",
        "reason": "outside_meal_window",
        "options": CONFIRM_OPTIONS,
    }


def test_端点前59秒仍按该分钟判断为窗口外(
    production_contract, build_service, clock_at
):
    # 04:59:59 截到 04:59，仍早于 05:00
    service = build_service(clock_at(4, 59, 59))

    result = service.resolve([])

    assert result["status"] == "needs_confirmation"
    assert result["reason"] == "outside_meal_window"
