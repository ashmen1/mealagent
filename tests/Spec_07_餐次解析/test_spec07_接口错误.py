from __future__ import annotations

import pytest


def assert_status_400(exc: Exception) -> None:
    assert exc.status_code == 400


def test_输入不是数组_字符串返回400(production_contract, build_service, clock_at):
    service = build_service(clock_at(7, 0))

    with pytest.raises(
        production_contract.MealPeriodResolutionValidationError
    ) as captured:
        service.resolve("早餐")

    assert_status_400(captured.value)


def test_输入不是数组_None返回400(production_contract, build_service, clock_at):
    service = build_service(clock_at(7, 0))

    with pytest.raises(
        production_contract.MealPeriodResolutionValidationError
    ) as captured:
        service.resolve(None)

    assert_status_400(captured.value)


def test_重复餐次返回400(production_contract, build_service, clock_at):
    service = build_service(clock_at(7, 0))

    with pytest.raises(
        production_contract.MealPeriodResolutionValidationError
    ) as captured:
        service.resolve(["早餐", "早餐"])

    assert_status_400(captured.value)


def test_重复下午茶返回400(production_contract, build_service, clock_at):
    service = build_service(clock_at(15, 0))

    with pytest.raises(
        production_contract.MealPeriodResolutionValidationError
    ) as captured:
        service.resolve(["下午茶", "下午茶"])

    assert_status_400(captured.value)


def test_未知餐次返回400(production_contract, build_service, clock_at):
    service = build_service(clock_at(7, 0))

    with pytest.raises(
        production_contract.MealPeriodResolutionValidationError
    ) as captured:
        service.resolve(["夜宵"])

    assert_status_400(captured.value)


def test_未知餐次与合法餐次混排返回400(production_contract, build_service, clock_at):
    service = build_service(clock_at(7, 0))

    with pytest.raises(
        production_contract.MealPeriodResolutionValidationError
    ) as captured:
        service.resolve(["早餐", "夜宵"])

    assert_status_400(captured.value)


def test_非字符串元素返回400(production_contract, build_service, clock_at):
    service = build_service(clock_at(7, 0))

    with pytest.raises(
        production_contract.MealPeriodResolutionValidationError
    ) as captured:
        service.resolve(["早餐", 123])

    assert_status_400(captured.value)


def test_时钟读取失败返回500(production_contract, build_service, failing_clock):
    service = build_service(failing_clock)

    with pytest.raises(
        production_contract.MealPeriodResolutionError
    ) as captured:
        service.resolve([])

    assert captured.value.status_code == 500


def test_非法时钟结果返回500且不用当前时间兜底(
    production_contract, build_service, invalid_clock
):
    service = build_service(invalid_clock)

    with pytest.raises(
        production_contract.MealPeriodResolutionError
    ) as captured:
        service.resolve([])

    assert captured.value.status_code == 500


def test_非法业务时区返回500(production_contract, clock_at):
    with pytest.raises(
        production_contract.MealPeriodResolutionError
    ) as captured:
        production_contract.MealPeriodResolutionService(
            clock=clock_at(12, 0),
            timezone_name="Not/AZone",
        )

    assert captured.value.status_code == 500
