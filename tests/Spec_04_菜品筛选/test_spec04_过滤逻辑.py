from __future__ import annotations

import pytest

from spec04_support import (
    build_integrated_constraints,
    build_integrated_dish,
    build_recipe_match,
    fake_driver,
    invoke_filter,
    production_contract,
)


def _record(name: str, tags: list[str], groups: list[str]) -> dict[str, object]:
    return {
        "recipe_name": name,
        "recipe_type": None,
        "matched_tags": tags,
        "matched_groups": groups,
    }


def test_空约束返回全部候选(invoke_filter, fake_driver):
    constraints = build_integrated_constraints()
    fake_driver.records = [
        _record("番茄炒蛋", [], []),
        _record("清蒸鲈鱼", [], []),
    ]

    result = invoke_filter(constraints, fake_driver)

    assert [r["recipe_name"] for r in result["dishes"][0]] == [
        "番茄炒蛋",
        "清蒸鲈鱼",
    ]
    assert result["unmatched_allergens"] == []


def test_查询使用参数化Cypher且不含字符串拼接(invoke_filter, fake_driver):
    constraints = build_integrated_constraints(
        meal_periods=["晚餐"],
        dishes=[
            build_integrated_dish(
                taste_preferences={"is_spicy": False},
                cuisines=["粤菜"],
            )
        ],
    )

    invoke_filter(constraints, fake_driver)

    assert fake_driver.executed_queries
    query, params = fake_driver.executed_queries[0]
    assert "$meal_periods" in query or "meal_periods" in query
    assert params["meal_periods"] == ["晚餐"]
    assert params["neg_taste"] == ["辣"]
    assert params["cuisines"] == ["粤菜"]


def test_每个候选查询只允许显式可推荐菜谱(invoke_filter, fake_driver):
    constraints = build_integrated_constraints(
        dishes=[
            build_integrated_dish(dish_type="菜"),
            build_integrated_dish(dish_type="汤"),
        ]
    )

    invoke_filter(constraints, fake_driver)

    recipe_queries = [
        query
        for query, _ in fake_driver.executed_queries
        if "MATCH (i:Ingredient)-[:part_of]->(d:Recipe)" in query
    ]
    assert len(recipe_queries) == 2
    assert all("d.is_recommendable = true" in query for query in recipe_queries)


def test_多餐次任一命中参数传递(invoke_filter, fake_driver):
    constraints = build_integrated_constraints(meal_periods=["午餐", "晚餐"])
    invoke_filter(constraints, fake_driver)
    query, params = fake_driver.executed_queries[0]
    assert params["meal_periods"] == ["午餐", "晚餐"]


def test_口味多值全部命中参数传递(invoke_filter, fake_driver):
    constraints = build_integrated_constraints(
        dishes=[
            build_integrated_dish(
                taste_preferences={"is_sweet": True, "is_light": True}
            )
        ]
    )
    invoke_filter(constraints, fake_driver)
    query, params = fake_driver.executed_queries[0]
    assert params["pos_taste"] == ["甜", "清淡"]
    assert "all(" in query or "all" in query


def test_否定口味硬排除参数传递(invoke_filter, fake_driver):
    constraints = build_integrated_constraints(
        dishes=[build_integrated_dish(taste_preferences={"is_spicy": False})]
    )
    invoke_filter(constraints, fake_driver)
    query, params = fake_driver.executed_queries[0]
    assert params["neg_taste"] == ["辣"]
    assert "NOT" in query or "not" in query


def test_菜系功效人群任一命中参数传递(invoke_filter, fake_driver):
    constraints = build_integrated_constraints(
        dishes=[
            build_integrated_dish(
                cuisines=["粤菜"],
                effects=["养胃健胃消食"],
                special_populations=["儿童"],
            )
        ]
    )
    invoke_filter(constraints, fake_driver)
    query, params = fake_driver.executed_queries[0]
    assert params["cuisines"] == ["粤菜"]
    assert params["effects"] == ["养胃健胃消食"]
    assert params["pops"] == ["儿童"]


def test_最长时间上限过滤参数传递(invoke_filter, fake_driver):
    constraints = build_integrated_constraints(max_total_time_minutes=45)
    invoke_filter(constraints, fake_driver)
    query, params = fake_driver.executed_queries[0]
    assert params["max_total_time_minutes"] == 45
    assert "total_time_lower_bound_minutes" in query


@pytest.mark.parametrize(
    ("max_difficulty", "expected_levels"),
    [
        ("简单", ["简单"]),
        ("中等", ["简单", "中等"]),
    ],
)
def test_难度上限转换为允许难度集合(
    max_difficulty,
    expected_levels,
    invoke_filter,
    fake_driver,
):
    constraints = build_integrated_constraints(
        max_difficulty=max_difficulty
    )

    invoke_filter(constraints, fake_driver)

    query, params = fake_driver.executed_queries[0]
    assert "difficulty" in query
    assert expected_levels in params.values()


def test_时间与难度同时存在时查询取交集(invoke_filter, fake_driver):
    constraints = build_integrated_constraints(
        max_total_time_minutes=45,
        max_difficulty="中等",
    )

    invoke_filter(constraints, fake_driver)

    query, params = fake_driver.executed_queries[0]
    assert "total_time_lower_bound_minutes" in query
    assert "difficulty" in query
    assert params["max_total_time_minutes"] == 45
    assert ["简单", "中等"] in params.values()


def test_三类必需食材参数传递(invoke_filter, fake_driver):
    constraints = build_integrated_constraints(
        dishes=[
            build_integrated_dish(
                required_ingredient_groups=[
                    {
                        "match": "all",
                        "items": [
                            {"kind": "ingredient", "value": "鸡蛋"},
                            {"kind": "category", "value": "蔬菜"},
                            {"kind": "concept", "value": "面"},
                        ],
                    }
                ]
            )
        ]
    )
    invoke_filter(constraints, fake_driver)
    query, params = fake_driver.executed_queries[0]
    assert params["requirement_groups"][0]["items"][0] == {
        "kind": "ingredient",
        "value": "鸡蛋",
    }
    assert params["requirement_groups"][0]["items"][1] == {
        "kind": "category",
        "value": "蔬菜",
    }
    assert params["requirement_groups"][0]["items"][2] == {
        "kind": "concept",
        "value": "面",
    }


def test_过敏展开为排除集合(invoke_filter, fake_driver):
    constraints = build_integrated_constraints(allergens=["海鲜"])
    invoke_filter(constraints, fake_driver)
    query, params = fake_driver.executed_queries[0]
    assert "excluded" in params
    assert "基围虾" in params["excluded"]
    assert "大闸蟹" in params["excluded"]
    assert "三文鱼" in params["excluded"]


def test_蟹类按精确成员展开且不包含蟹味菇(invoke_filter, fake_driver):
    constraints = build_integrated_constraints(allergens=["蟹类"])

    result = invoke_filter(constraints, fake_driver)

    _, params = fake_driver.executed_queries[0]
    assert params["excluded"] == [
        "大闸蟹",
        "梭子蟹",
        "螃蟹",
        "蟹肉棒",
        "蟹黄",
        "蟹黄/蟹膏",
        "青蟹",
    ]
    assert "蟹味菇" not in params["excluded"]
    assert result["unmatched_allergens"] == []


@pytest.mark.parametrize("allergen", ["花生", "芒果", "啤酒"])
def test_食材型过敏词按标准名排除(
    allergen, invoke_filter, fake_driver
):
    constraints = build_integrated_constraints(allergens=[allergen])
    invoke_filter(constraints, fake_driver)
    query, params = fake_driver.executed_queries[-1]
    assert allergen in params["excluded"]


def test_可用食材核心全在辅料不限参数传递(invoke_filter, fake_driver):
    constraints = build_integrated_constraints(
        available_ingredients=["番茄", "鸡蛋"]
    )
    invoke_filter(constraints, fake_driver)
    query, params = fake_driver.executed_queries[0]
    assert params["available_ingredients"] == ["番茄", "鸡蛋"]
    assert "is_core_ingredient" in query


def test_未知可用食材通过图内标准名判断后忽略(invoke_filter, fake_driver):
    constraints = build_integrated_constraints(
        available_ingredients=["不存在的食材"]
    )

    invoke_filter(constraints, fake_driver)

    query, _ = fake_driver.executed_queries[0]
    assert "MATCH (available:Ingredient)" in query
    assert "NOT EXISTS" in query


def test_整桌总数和组内数量都不产生截断参数(invoke_filter, fake_driver):
    constraints = build_integrated_constraints(
        total_dish_count=4,
        dishes=[build_integrated_dish(count=4, dish_type="菜")]
    )
    invoke_filter(constraints, fake_driver)
    query, params = fake_driver.executed_queries[0]
    assert "count" not in params
    assert "total_dish_count" not in params
    assert "LIMIT" not in query


def test_噪声标签不进入过滤参数(invoke_filter, fake_driver):
    constraints = build_integrated_constraints(
        dishes=[build_integrated_dish(cuisines=["粤菜"])]
    )
    invoke_filter(constraints, fake_driver)
    query, params = fake_driver.executed_queries[0]
    assert params["cuisines"] == ["粤菜"]
    assert all("春节" not in str(p) for p in params.values())


def test_候选排序确定(invoke_filter, fake_driver):
    constraints = build_integrated_constraints(
        meal_periods=["晚餐"],
        dishes=[build_integrated_dish(cuisines=["粤菜"])],
    )
    fake_driver.records = [
        _record("清蒸鲈鱼", ["晚餐", "甜"], ["餐次", "口味"]),
        _record(
            "番茄炒蛋",
            ["晚餐", "粤菜", "甜"],
            ["餐次", "菜系", "口味"],
        ),
    ]
    result = invoke_filter(constraints, fake_driver)
    assert [r["recipe_name"] for r in result["dishes"][0]] == [
        "番茄炒蛋",
        "清蒸鲈鱼",
    ]
    assert result["dishes"][0][0]["matched_tags"] == ["晚餐", "粤菜"]
    assert result["dishes"][0][1]["matched_tags"] == ["晚餐"]
