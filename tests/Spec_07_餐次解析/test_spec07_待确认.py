from __future__ import annotations

from .spec07_support import CONFIRM_OPTIONS


def test_多个不同餐次返回待确认_multiple(production_contract, build_service, clock_at):
    service = build_service(clock_at(7, 0))

    result = service.resolve(["早餐", "晚餐"])

    assert result == {
        "status": "needs_confirmation",
        "meal_period": None,
        "source": "explicit",
        "reason": "multiple_meal_periods",
        "options": CONFIRM_OPTIONS,
    }


def test_下午茶与正餐混排返回待确认_multiple(production_contract, build_service, clock_at):
    service = build_service(clock_at(12, 0))

    result = service.resolve(["早餐", "下午茶"])

    assert result == {
        "status": "needs_confirmation",
        "meal_period": None,
        "source": "explicit",
        "reason": "multiple_meal_periods",
        "options": CONFIRM_OPTIONS,
    }


def test_单个下午茶返回待确认_unsupported(production_contract, build_service, clock_at):
    service = build_service(clock_at(15, 0))

    result = service.resolve(["下午茶"])

    assert result == {
        "status": "needs_confirmation",
        "meal_period": None,
        "source": "explicit",
        "reason": "unsupported_meal_period",
        "options": CONFIRM_OPTIONS,
    }


def test_待确认是正常业务结果不抛异常(production_contract, build_service, clock_at):
    # 三个待确认场景都不抛异常，正常返回结构化结果
    service = build_service(clock_at(22, 0))

    results = [
        service.resolve([]),
        service.resolve(["早餐", "晚餐"]),
        service.resolve(["下午茶"]),
    ]

    assert all(result["status"] == "needs_confirmation" for result in results)


def test_确认餐次后以明确餐次重新解析(production_contract, build_service, clock_at):
    # 时钟固定在 22:00（窗口外），先待确认，再用明确餐次重新解析
    service = build_service(clock_at(22, 0))

    first = service.resolve([])
    assert first["status"] == "needs_confirmation"
    assert first["reason"] == "outside_meal_window"

    second = service.resolve(["晚餐"])

    assert second == {
        "status": "resolved",
        "meal_period": "晚餐",
        "source": "explicit",
        "reason": None,
        "options": [],
    }
