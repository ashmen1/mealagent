from __future__ import annotations

from .spec07_support import RESOLUTION_KEYS


def test_明确早餐直接返回_resolved_explicit(production_contract, build_service, clock_at):
    # 时钟在晚餐时段，明确餐次不得被当前时间覆盖
    service = build_service(clock_at(19, 0))

    result = service.resolve(["早餐"])

    assert result == {
        "status": "resolved",
        "meal_period": "早餐",
        "source": "explicit",
        "reason": None,
        "options": [],
    }


def test_明确午餐直接返回_resolved_explicit(production_contract, build_service, clock_at):
    # 时钟在早餐时段，明确餐次不得被当前时间覆盖
    service = build_service(clock_at(7, 0))

    result = service.resolve(["午餐"])

    assert result == {
        "status": "resolved",
        "meal_period": "午餐",
        "source": "explicit",
        "reason": None,
        "options": [],
    }


def test_明确晚餐直接返回_resolved_explicit(production_contract, build_service, clock_at):
    # 时钟在午餐时段，明确餐次不得被当前时间覆盖
    service = build_service(clock_at(12, 0))

    result = service.resolve(["晚餐"])

    assert result == {
        "status": "resolved",
        "meal_period": "晚餐",
        "source": "explicit",
        "reason": None,
        "options": [],
    }


def test_空数组早餐时段返回_resolved_current_time(production_contract, build_service, clock_at):
    service = build_service(clock_at(7, 30))

    result = service.resolve([])

    assert result == {
        "status": "resolved",
        "meal_period": "早餐",
        "source": "current_time",
        "reason": None,
        "options": [],
    }


def test_空数组午餐时段返回_resolved_current_time(production_contract, build_service, clock_at):
    service = build_service(clock_at(12, 0))

    result = service.resolve([])

    assert result == {
        "status": "resolved",
        "meal_period": "午餐",
        "source": "current_time",
        "reason": None,
        "options": [],
    }


def test_空数组晚餐时段返回_resolved_current_time(production_contract, build_service, clock_at):
    service = build_service(clock_at(18, 30))

    result = service.resolve([])

    assert result == {
        "status": "resolved",
        "meal_period": "晚餐",
        "source": "current_time",
        "reason": None,
        "options": [],
    }


def test_显式注入AsiaShanghai时区正常解析(production_contract, clock_at):
    service = production_contract.MealPeriodResolutionService(
        clock=clock_at(8, 0),
        timezone_name="Asia/Shanghai",
    )

    result = service.resolve([])

    assert result["status"] == "resolved"
    assert result["meal_period"] == "早餐"
    assert result["source"] == "current_time"


def test_解析结果不夹带确认文案等额外字段(production_contract, build_service, clock_at):
    service = build_service(clock_at(7, 0))

    result = service.resolve(["早餐"])

    assert set(result.keys()) == RESOLUTION_KEYS
