from __future__ import annotations

import pytest

from .spec10_support import (
    build_candidate,
    build_filtering_result,
    build_planning_result,
    build_selected_dish,
)


def test_菜品与理由保持最终选择顺序并准确记录数组路径(invoke_build):
    filtering_result = build_filtering_result(
        dishes=[
            [
                build_candidate("菜A", ["晚餐"], ["餐次"]),
                build_candidate("未选中", [], []),
                build_candidate("菜B", ["清淡"], ["口味"]),
            ]
        ]
    )
    planning_result = build_planning_result(
        selected_dishes=[
            build_selected_dish(recipe_name="菜B"),
            build_selected_dish(recipe_name="菜A"),
        ]
    )

    result = invoke_build(filtering_result, planning_result)

    assert [
        item["recipe_name"] for item in result["dish_recommendations"]
    ] == ["菜B", "菜A"]
    assert result["dish_recommendations"][0]["reasons"][0]["sources"] == [
        {
            "component": "menu_planning",
            "paths": [
                "selected_dishes[0].dish_constraint_index",
                "selected_dishes[0].recipe_name",
            ],
        },
        {
            "component": "dish_filtering",
            "paths": [
                "dishes[0][2].matched_tags",
                "dishes[0][2].matched_groups",
            ],
        },
    ]
    assert result["dish_recommendations"][1]["reasons"][0]["sources"] == [
        {
            "component": "menu_planning",
            "paths": [
                "selected_dishes[1].dish_constraint_index",
                "selected_dishes[1].recipe_name",
            ],
        },
        {
            "component": "dish_filtering",
            "paths": [
                "dishes[0][0].matched_tags",
                "dishes[0][0].matched_groups",
            ],
        },
    ]


def test_同组多标签保持标签原顺序且理由按固定组顺序输出(invoke_build):
    filtering_result = build_filtering_result(
        dishes=[
            [
                build_candidate(
                    "综合菜",
                    ["咸", "晚餐", "清淡", "粤菜", "助眠", "儿童"],
                    ["人群", "功效", "菜系", "口味", "餐次"],
                )
            ]
        ]
    )
    planning_result = build_planning_result(
        selected_dishes=[build_selected_dish(recipe_name="综合菜")]
    )

    result = invoke_build(filtering_result, planning_result)

    reasons = result["dish_recommendations"][0]["reasons"]
    assert [reason["matched_group"] for reason in reasons] == [
        "餐次",
        "口味",
        "菜系",
        "功效",
        "人群",
    ]
    assert [reason["matched_tags"] for reason in reasons] == [
        ["晚餐"],
        ["咸", "清淡"],
        ["粤菜"],
        ["助眠"],
        ["儿童"],
    ]
    assert [reason["text"] for reason in reasons] == [
        "综合菜适合本次晚餐。",
        "综合菜符合本次咸、清淡口味偏好。",
        "综合菜符合本次粤菜偏好。",
        "综合菜匹配本次提出的助眠功效标签。",
        "综合菜匹配本次提出的儿童人群标签。",
    ]


def test_无命中标签返回空理由而不生成通用文案(invoke_build):
    filtering_result = build_filtering_result(
        dishes=[[build_candidate(matched_tags=[], matched_groups=[])]]
    )

    result = invoke_build(filtering_result, build_planning_result())

    assert result["dish_recommendations"][0]["reasons"] == []


def test_对应组找不到最终菜品返回500且不跨组搜索(
    assert_reason_error,
):
    filtering_result = build_filtering_result(
        dishes=[
            [build_candidate("组0菜")],
            [build_candidate("白灼芥蓝")],
        ]
    )

    assert_reason_error(filtering_result, build_planning_result(), 500)


def test_菜品组索引不存在返回500(assert_reason_error):
    planning_result = build_planning_result(
        selected_dishes=[build_selected_dish(dish_constraint_index=3)]
    )

    assert_reason_error(build_filtering_result(), planning_result, 500)


def test_对应组存在多个同名候选返回500(assert_reason_error):
    filtering_result = build_filtering_result(
        dishes=[
            [
                build_candidate("白灼芥蓝"),
                build_candidate("白灼芥蓝", ["粤菜"], ["菜系"]),
            ]
        ]
    )

    assert_reason_error(filtering_result, build_planning_result(), 500)


@pytest.mark.parametrize(
    "matched_tags,matched_groups",
    [
        (["自定义标签"], ["口味"]),
        (["晚餐"], ["餐次", "未知组"]),
        (["晚餐"], ["餐次", "口味"]),
        (["晚餐", "清淡"], ["餐次"]),
    ],
    ids=[
        "未知标签",
        "未知标签组",
        "标签组没有对应标签",
        "标签所属组未声明",
    ],
)
def test_标签与标签组关系非法返回500(
    matched_tags,
    matched_groups,
    assert_reason_error,
):
    filtering_result = build_filtering_result(
        dishes=[
            [
                build_candidate(
                    matched_tags=matched_tags,
                    matched_groups=matched_groups,
                )
            ]
        ]
    )

    assert_reason_error(filtering_result, build_planning_result(), 500)
