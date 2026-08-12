from __future__ import annotations

import copy
from decimal import Decimal

import pytest

from .spec06_support import (
    NUTRIENT_FIELDS,
    build_candidate,
    build_dish,
    build_nutrition,
    build_planning_input,
)


def test_正常路径返回唯一最优菜单及营养评分(invoke_plan):
    planning_input = build_planning_input()

    result = invoke_plan(planning_input)

    assert result["profile_id"] == 25
    assert result["dialogue_id"] == 1
    assert result["meal_period"] == "午餐"
    assert result["diner_count"] == 1
    assert [dish["recipe_name"] for dish in result["selected_dishes"]] == [
        "标准午餐"
    ]
    assert result["selected_dishes"][0]["dish_constraint_index"] == 0
    assert result["total_nutrition"] == build_nutrition()
    assert result["per_person_nutrition"] == build_nutrition()
    assert result["nutrition_score"] == 16
    assert set(result["nutrient_grades"]) == set(NUTRIENT_FIELDS)
    assert result["applied_health_constraints"] == []
    assert result["unapplied_health_constraints"] == []


@pytest.mark.parametrize("bad_value", [0, -1, True, "25"])
def test_profile_id必须为正整数(bad_value, assert_plan_error):
    assert_plan_error(
        build_planning_input(profile_id=bad_value),
        expected_status=400,
    )


@pytest.mark.parametrize("bad_value", [0, -1, True, "1"])
def test_dialogue_id必须为正整数(bad_value, assert_plan_error):
    assert_plan_error(
        build_planning_input(dialogue_id=bad_value),
        expected_status=400,
    )


@pytest.mark.parametrize("bad_value", [0, -1, True, "2"])
def test_用餐人数只能为正整数或null(bad_value, assert_plan_error):
    assert_plan_error(
        build_planning_input(diner_count=bad_value),
        expected_status=400,
    )


@pytest.mark.parametrize("bad_meal", ["下午茶", "", None, ["午餐"]])
def test_餐次只允许单个正餐(bad_meal, assert_plan_error):
    planning_input = build_planning_input()
    planning_input["meal_period"] = bad_meal
    assert_plan_error(planning_input, expected_status=400)


def test_菜品要求不能为空(assert_plan_error):
    assert_plan_error(
        build_planning_input(dishes=[]),
        expected_status=400,
    )


@pytest.mark.parametrize("bad_count", [0, -1, True, "1"])
def test_明确菜品数量必须为正整数(bad_count, assert_plan_error):
    planning_input = build_planning_input(
        dishes=[build_dish(count=bad_count)]
    )
    assert_plan_error(planning_input, expected_status=400)


@pytest.mark.parametrize("bad_type", ["", "饮料", None, 1])
def test_菜品类型只允许约定枚举(bad_type, assert_plan_error):
    planning_input = build_planning_input()
    planning_input["dishes"][0]["dish_type"] = bad_type
    assert_plan_error(planning_input, expected_status=400)


@pytest.mark.parametrize("bad_name", ["", None, 1])
def test_候选菜谱名必须为非空字符串(bad_name, assert_plan_error):
    planning_input = build_planning_input()
    planning_input["dishes"][0]["candidates"][0]["recipe_name"] = bad_name
    assert_plan_error(planning_input, expected_status=400)


@pytest.mark.parametrize("missing_field", NUTRIENT_FIELDS)
def test_候选必须带完整九项营养(missing_field, assert_plan_error):
    planning_input = build_planning_input()
    del planning_input["dishes"][0]["candidates"][0]["nutrition"][missing_field]
    assert_plan_error(planning_input, expected_status=400)


@pytest.mark.parametrize("missing_field", NUTRIENT_FIELDS)
def test_单餐营养目标必须完整(missing_field, assert_plan_error):
    planning_input = build_planning_input()
    del planning_input["nutrient_targets"][missing_field]
    assert_plan_error(planning_input, expected_status=400)


@pytest.mark.parametrize("bad_value", [Decimal("-0.01"), "未知", None, True])
def test_候选营养值必须为非负数(bad_value, assert_plan_error):
    planning_input = build_planning_input()
    planning_input["dishes"][0]["candidates"][0]["nutrition"][
        "energy_kcal"
    ] = bad_value
    assert_plan_error(planning_input, expected_status=400)


def test_重复候选菜谱名返回400(assert_plan_error):
    duplicate = build_candidate("重复菜谱")
    planning_input = build_planning_input(
        dishes=[build_dish(candidates=[duplicate, copy.deepcopy(duplicate)])]
    )
    assert_plan_error(planning_input, expected_status=400)


def test_候选营养含额外字段返回400(assert_plan_error):
    planning_input = build_planning_input()
    planning_input["dishes"][0]["candidates"][0]["nutrition"][
        "vitamin_c_mg"
    ] = Decimal("20")
    assert_plan_error(planning_input, expected_status=400)


def test_未解析过敏词阻止规划并返回具体词(assert_plan_error):
    error = assert_plan_error(
        build_planning_input(unmatched_allergens=["贝壳类"]),
        expected_status=422,
    )
    assert "贝壳类" in str(error)
