from __future__ import annotations

import pytest

from spec04_support import (
    assert_filter_error,
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


def test_正常路径返回匹配的菜谱候选(invoke_filter, fake_driver):
    """端点正常路径：按完整约束过滤，返回每组候选，顺序与输入一致。"""
    constraints = build_integrated_constraints(
        meal_periods=["晚餐"],
        max_total_time_minutes=45,
        available_ingredients=["番茄", "鸡蛋"],
        allergens=["海鲜"],
        dishes=[
            build_integrated_dish(
                count=4,
                dish_type="菜",
                taste_preferences={"is_light": True, "is_spicy": False},
                cuisines=["粤菜"],
                required_ingredient_groups=[
                    {
                        "match": "all",
                        "items": [
                            {"kind": "ingredient", "value": "鸡蛋"}
                        ],
                    }
                ],
            ),
            build_integrated_dish(
                count=1,
                dish_type="汤",
                cuisines=["粤菜"],
                required_ingredient_groups=[
                    {
                        "match": "all",
                        "items": [
                            {"kind": "concept", "value": "面"}
                        ],
                    }
                ],
            ),
        ],
    )
    fake_driver.set_records_by_query(
        [
            [_record("白灼芥蓝", ["晚餐", "粤菜", "清淡"], ["餐次", "菜系", "口味"])],
            [_record("粤式上汤面", ["晚餐", "粤菜"], ["餐次", "菜系"])],
        ]
    )

    result = invoke_filter(constraints, fake_driver)

    assert len(result["dishes"]) == 2
    assert [r["recipe_name"] for r in result["dishes"][0]] == ["白灼芥蓝"]
    assert [r["recipe_name"] for r in result["dishes"][1]] == ["粤式上汤面"]
    assert result["dishes"][0][0]["matched_groups"] == ["餐次", "口味", "菜系"]
    assert result["unmatched_allergens"] == []


def test_每组独立过滤互不影响(invoke_filter, fake_driver):
    constraints = build_integrated_constraints(
        dishes=[
            build_integrated_dish(taste_preferences={"is_sweet": True}),
            build_integrated_dish(cuisines=["粤菜"]),
        ]
    )
    invoke_filter(constraints, fake_driver)
    assert len(fake_driver.executed_queries) == 2


def test_无候选返回空列表不报错(invoke_filter, fake_driver):
    constraints = build_integrated_constraints(
        dishes=[build_integrated_dish(cuisines=["东北菜"])]
    )
    fake_driver.records = []
    result = invoke_filter(constraints, fake_driver)
    assert result["dishes"][0] == []
    assert result["unmatched_allergens"] == []


def test_unmatched过敏词进报告且不参与排除(invoke_filter, fake_driver):
    constraints = build_integrated_constraints(allergens=["贝壳类"])
    fake_driver.records = [_record("番茄炒蛋", [], [])]
    result = invoke_filter(constraints, fake_driver)
    assert result["unmatched_allergens"] == ["贝壳类"]
    assert len(result["dishes"][0]) == 1


def test_Neo4j不可达时返回500(
    invoke_filter, fake_driver, assert_filter_error
):
    constraints = build_integrated_constraints()
    fake_driver.fail_query = True
    assert_filter_error(
        constraints,
        fake_driver,
        expected_status=500,
    )


def test_结果顺序与输入dishes一致(invoke_filter, fake_driver):
    constraints = build_integrated_constraints(
        dishes=[
            build_integrated_dish(cuisines=["粤菜"]),
            build_integrated_dish(effects=["助眠"]),
        ]
    )
    fake_driver.set_records_by_query(
        [
            [_record("清蒸鲈鱼", [], [])],
            [],
        ]
    )
    result = invoke_filter(constraints, fake_driver)
    assert len(result["dishes"]) == 2
    assert [r["recipe_name"] for r in result["dishes"][0]] == ["清蒸鲈鱼"]
    assert result["dishes"][1] == []
