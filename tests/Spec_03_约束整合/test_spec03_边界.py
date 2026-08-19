from __future__ import annotations

import pytest

from spec03_support import (
    assert_integration_error,
    build_dialogue_constraints,
    build_dish,
    build_multi_turn_dialogue_constraints,
    build_profile_constraints,
    invoke_integrate,
    production_contract,
)


def test_空档案约束不改变对话业务约束(invoke_integrate):
    profile_constraints = build_profile_constraints()
    dialogue_constraints = build_dialogue_constraints(
        dialogue_id=3,
        meal_periods=["午餐"],
        dishes=[build_dish(taste_preferences={"is_light": True})],
        evidence={
            "meal_periods[0]": "午餐",
            "dishes[0].taste_preferences.is_light": "清淡",
        },
    )

    result = invoke_integrate(profile_constraints, dialogue_constraints)

    assert result["meal_periods"] == ["午餐"]
    assert result["dishes"] == dialogue_constraints["dishes"]
    assert result["allergens"] == []
    assert result["has_conflicts"] is False
    assert result["conflicts"] == []


def test_合法整数极值保持不变(invoke_integrate):
    maximum = 2_147_483_647
    profile_constraints = build_profile_constraints(profile_id=50)
    dialogue_constraints = build_dialogue_constraints(
        dialogue_id=maximum,
        diner_count=maximum,
        max_total_time_minutes=maximum,
        dishes=[build_dish(count=maximum, dish_type="菜")],
        evidence={
            "diner_count": f"{maximum}个人",
            "max_total_time_minutes": f"{maximum}分钟",
            "dishes[0].count": f"{maximum}道菜",
            "dishes[0].dish_type": "菜",
        },
    )

    result = invoke_integrate(profile_constraints, dialogue_constraints)

    assert result["profile_id"] == 50
    assert result["dialogue_id"] == maximum
    assert result["diner_count"] == maximum
    assert result["max_total_time_minutes"] == maximum
    assert result["dishes"][0]["count"] == maximum


@pytest.mark.parametrize(
    ("allergen", "required_ingredient"),
    [
        ("海鲜", {"kind": "ingredient", "value": "虾"}),
        ("坚果", {"kind": "ingredient", "value": "花生"}),
    ],
)
def test_非同名语义关系不记录冲突(
    allergen,
    required_ingredient,
    invoke_integrate,
):
    profile_constraints = build_profile_constraints(allergens=[allergen])
    dialogue_constraints = build_dialogue_constraints(
        dishes=[
            build_dish(
                required_ingredient_groups=[
                    {"match": "all", "items": [required_ingredient]}
                ]
            )
        ],
        evidence={
            "dishes[0].required_ingredient_groups[0].match": required_ingredient[
                "value"
            ],
            "dishes[0].required_ingredient_groups[0].items[0].value": required_ingredient[
                "value"
            ],
        },
    )

    result = invoke_integrate(profile_constraints, dialogue_constraints)

    assert result["has_conflicts"] is False
    assert result["conflicts"] == []


def test_可用食材与过敏词同名不记录冲突(invoke_integrate):
    profile_constraints = build_profile_constraints(allergens=["鸡蛋"])
    dialogue_constraints = build_dialogue_constraints(
        available_ingredients=["鸡蛋", "番茄"],
        evidence={
            "available_ingredients[0]": "鸡蛋",
            "available_ingredients[1]": "番茄",
        },
    )

    result = invoke_integrate(profile_constraints, dialogue_constraints)

    assert result["available_ingredients"] == ["鸡蛋", "番茄"]
    assert result["allergens"] == ["鸡蛋"]
    assert result["has_conflicts"] is False
    assert result["conflicts"] == []


def test_冲突证据缺失时返回400(
    assert_integration_error,
):
    profile_constraints = build_profile_constraints(allergens=["花生"])
    dialogue_constraints = build_dialogue_constraints(
        dishes=[
            build_dish(
                required_ingredient_groups=[
                    {
                        "match": "all",
                        "items": [
                            {"kind": "ingredient", "value": "花生"}
                        ],
                    }
                ]
            )
        ],
        evidence={},
    )

    assert_integration_error(profile_constraints, dialogue_constraints)


@pytest.mark.parametrize("duplicate_source", ["profile", "dialogue"])
def test_重复人群输入不符合上游Spec时返回400(
    duplicate_source,
    assert_integration_error,
):
    profile_constraints = build_profile_constraints()
    dialogue_constraints = build_dialogue_constraints()
    if duplicate_source == "profile":
        profile_constraints["special_populations"] = ["孕妇", "孕妇"]
    else:
        dialogue_constraints["dishes"] = [
            build_dish(special_populations=["儿童", "儿童"])
        ]
        dialogue_constraints["evidence"] = {
            "dishes[0].special_populations[0]": "儿童",
            "dishes[0].special_populations[1]": "儿童",
        }

    assert_integration_error(profile_constraints, dialogue_constraints)


@pytest.mark.parametrize("invalid_source", ["profile", "dialogue"])
def test_输入不符合上游Spec时返回400(
    invalid_source,
    assert_integration_error,
):
    profile_constraints = build_profile_constraints()
    dialogue_constraints = build_dialogue_constraints()
    if invalid_source == "profile":
        profile_constraints.pop("allergens")
    else:
        dialogue_constraints.pop("dishes")

    assert_integration_error(profile_constraints, dialogue_constraints)


@pytest.mark.parametrize(
    "partial_field",
    ["total_dish_count", "max_difficulty"],
)
def test_新字段只出现一个时拒绝混合结构(
    partial_field,
    assert_integration_error,
):
    dialogue_constraints = build_dialogue_constraints()
    dialogue_constraints[partial_field] = (
        4 if partial_field == "total_dish_count" else "简单"
    )

    assert_integration_error(
        build_profile_constraints(),
        dialogue_constraints,
    )


def test_完整多轮结构包含未知字段时返回400(
    assert_integration_error,
):
    dialogue_constraints = build_multi_turn_dialogue_constraints()
    dialogue_constraints["difficulty"] = "简单"

    assert_integration_error(
        build_profile_constraints(),
        dialogue_constraints,
    )


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("total_dish_count", 0),
        ("total_dish_count", True),
        ("max_difficulty", "复杂"),
        ("max_difficulty", "简单点"),
    ],
)
def test_多轮新增字段不符合契约时返回400(
    field,
    bad_value,
    assert_integration_error,
):
    dialogue_constraints = build_multi_turn_dialogue_constraints()
    dialogue_constraints[field] = bad_value

    assert_integration_error(
        build_profile_constraints(),
        dialogue_constraints,
    )
