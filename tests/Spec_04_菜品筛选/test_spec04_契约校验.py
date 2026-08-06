from __future__ import annotations

import pytest

from spec04_support import (
    assert_filter_error,
    build_integrated_constraints,
    build_integrated_dish,
    fake_driver,
    invoke_filter,
    production_contract,
)


@pytest.mark.parametrize("missing_key", ["profile_id", "dishes", "allergens"])
def test_缺少必填字段时返回400(missing_key, assert_filter_error, fake_driver):
    constraints = build_integrated_constraints()
    constraints.pop(missing_key)
    assert_filter_error(constraints, fake_driver, expected_status=400)


@pytest.mark.parametrize(
    "invalid_key",
    ["meal_periods", "allergens", "available_ingredients"],
)
def test_非法枚举值时返回400(invalid_key, assert_filter_error, fake_driver):
    constraints = build_integrated_constraints()
    if invalid_key == "meal_periods":
        constraints["meal_periods"] = ["夜宵"]
    elif invalid_key == "allergens":
        constraints["allergens"] = [123]
    else:
        constraints["available_ingredients"] = [None]
    assert_filter_error(constraints, fake_driver, expected_status=400)


def test_类型错误时返回400(assert_filter_error, fake_driver):
    constraints = build_integrated_constraints(profile_id="不是数字")
    assert_filter_error(constraints, fake_driver, expected_status=400)


def test_has_conflicts为true时返回400且不查询(
    assert_filter_error, fake_driver
):
    constraints = build_integrated_constraints(
        has_conflicts=True,
        conflicts=[
            {
                "code": "allergen_required_ingredient",
                "dish_index": 0,
                "profile_path": "allergens[0]",
                "dialogue_path": (
                    "dishes[0].required_ingredients[0].value"
                ),
                "allergen": "花生",
                "required_ingredient": {
                    "kind": "ingredient",
                    "value": "花生",
                },
                "dialogue_evidence": "要花生",
            }
        ],
    )
    assert_filter_error(constraints, fake_driver, expected_status=400)
    assert fake_driver.executed_queries == []


def test_极值保持不变(invoke_filter, fake_driver):
    maximum = 2_147_483_647
    constraints = build_integrated_constraints(
        profile_id=maximum,
        dialogue_id=maximum,
        diner_count=maximum,
        max_total_time_minutes=maximum,
        dishes=[build_integrated_dish(count=maximum, dish_type="菜")],
    )
    fake_driver.records = []
    # 合法极值输入应正常过滤，不因数值过大被校验拒绝
    result = invoke_filter(constraints, fake_driver)
    assert result["dishes"] == [[]]


def test_数组内重复值时返回400(assert_filter_error, fake_driver):
    constraints = build_integrated_constraints(
        meal_periods=["晚餐", "晚餐"],
        dishes=[build_integrated_dish(cuisines=["粤菜", "粤菜"])],
    )
    assert_filter_error(constraints, fake_driver, expected_status=400)


def test_空dishes时返回400(assert_filter_error, fake_driver):
    constraints = build_integrated_constraints(dishes=[])
    assert_filter_error(constraints, fake_driver, expected_status=400)


@pytest.mark.parametrize(
    "dish_overrides",
    [
        {"dish_type": "甜品"},
        {"taste_preferences": {"is_sweet": "yes"}},
        {"cuisines": ["鲁菜"]},
        {"effects": ["美白"]},
        {"special_populations": [123]},
        {"required_ingredients": [{"kind": "raw", "value": "番茄"}]},
        {"required_ingredients": [{"kind": "ingredient"}]},
    ],
)
def test_非法dish字段时返回400(dish_overrides, assert_filter_error, fake_driver):
    constraints = build_integrated_constraints(
        dishes=[build_integrated_dish(**dish_overrides)]
    )
    assert_filter_error(constraints, fake_driver, expected_status=400)
