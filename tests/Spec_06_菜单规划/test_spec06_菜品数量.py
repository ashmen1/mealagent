from __future__ import annotations

import pytest

from .spec06_support import (
    build_candidate,
    build_dish,
    build_planning_input,
    candidates_with_split_nutrition,
)


@pytest.mark.parametrize(
    ("diner_count", "expected_count"),
    [(None, 1), (1, 1), (2, 2), (3, 3), (4, 3), (5, 4)],
)
def test_人数决定默认总菜数(diner_count, expected_count, invoke_plan):
    planning_input = build_planning_input(
        diner_count=diner_count,
        dishes=[
            build_dish(
                count=None,
                candidates=candidates_with_split_nutrition(expected_count),
            )
        ],
    )

    result = invoke_plan(planning_input)

    assert result["diner_count"] == (diner_count or 1)
    assert len(result["selected_dishes"]) == expected_count


def test_全部数量明确时忽略默认总菜数(invoke_plan):
    planning_input = build_planning_input(
        diner_count=10,
        dishes=[
            build_dish(
                count=1,
                dish_type="菜",
                candidates=[build_candidate("菜品")],
            ),
            build_dish(
                count=1,
                dish_type="汤",
                candidates=[build_candidate("汤品", recipe_type="汤")],
            ),
        ],
    )

    result = invoke_plan(planning_input)

    assert len(result["selected_dishes"]) == 2
    assert [dish["dish_constraint_index"] for dish in result["selected_dishes"]] == [
        0,
        1,
    ]


def test_部分数量未明确时每项至少一道并分配剩余名额(invoke_plan):
    planning_input = build_planning_input(
        diner_count=5,
        dishes=[
            build_dish(
                count=1,
                dish_type="主食",
                candidates=[build_candidate("米饭", recipe_type="主食")],
            ),
            build_dish(
                count=None,
                dish_type="菜",
                candidates=[
                    build_candidate("炒菜一"),
                    build_candidate("炒菜二"),
                ],
            ),
            build_dish(
                count=None,
                dish_type="汤",
                candidates=[build_candidate("汤", recipe_type="汤")],
            ),
        ],
    )

    result = invoke_plan(planning_input)

    indexes = [dish["dish_constraint_index"] for dish in result["selected_dishes"]]
    assert len(indexes) == 4
    assert indexes.count(0) == 1
    assert indexes.count(1) >= 1
    assert indexes.count(2) >= 1


def test_明确数量大于默认数时以明确数量为准(invoke_plan):
    planning_input = build_planning_input(
        diner_count=2,
        dishes=[
            build_dish(
                count=3,
                candidates=candidates_with_split_nutrition(3),
            )
        ],
    )
    result = invoke_plan(planning_input)
    assert len(result["selected_dishes"]) == 3


def test_空候选返回422(assert_plan_error):
    planning_input = build_planning_input(
        dishes=[build_dish(count=1, candidates=[])]
    )
    assert_plan_error(planning_input, expected_status=422)


def test_单项候选数量不足返回422(assert_plan_error):
    planning_input = build_planning_input(
        dishes=[build_dish(count=2, candidates=[build_candidate("唯一菜谱")])]
    )
    assert_plan_error(planning_input, expected_status=422)


def test_跨菜品要求同名候选不能重复占位(assert_plan_error):
    same_candidate = build_candidate("番茄炒蛋")
    planning_input = build_planning_input(
        diner_count=2,
        dishes=[
            build_dish(count=1, candidates=[same_candidate]),
            build_dish(count=1, candidates=[same_candidate]),
        ],
    )
    assert_plan_error(planning_input, expected_status=422)


def test_固定整份营养求和且仅人均结果除以人数(invoke_plan):
    candidates = candidates_with_split_nutrition(2)
    planning_input = build_planning_input(
        diner_count=2,
        dishes=[build_dish(count=2, candidates=candidates)],
    )

    result = invoke_plan(planning_input)

    assert result["total_nutrition"]["energy_kcal"] == 800
    assert result["per_person_nutrition"]["energy_kcal"] == 400
    assert [
        dish["nutrition"]["energy_kcal"]
        for dish in result["selected_dishes"]
    ] == [400, 400]
