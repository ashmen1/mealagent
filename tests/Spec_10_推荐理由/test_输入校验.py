from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest

from .spec10_support import (
    NUTRIENTS,
    build_candidate,
    build_filtering_result,
    build_grade,
    build_planning_result,
    build_selected_dish,
)


@pytest.mark.parametrize("bad_filtering", [None, [], "结果", 1, True, {}])
def test_筛选结果顶层结构非法返回400(
    bad_filtering,
    assert_reason_error,
):
    assert_reason_error(bad_filtering, build_planning_result(), 400)


@pytest.mark.parametrize("bad_planning", [None, [], "结果", 1, True, {}])
def test_规划结果顶层结构非法返回400(
    bad_planning,
    assert_reason_error,
):
    assert_reason_error(build_filtering_result(), bad_planning, 400)


@pytest.mark.parametrize(
    "field",
    [
        "profile_id",
        "dialogue_id",
        "selected_dishes",
        "nutrition_score",
        "nutrient_grades",
        "applied_health_constraints",
    ],
)
def test_规划结果缺少必需字段返回400(field, assert_reason_error):
    planning_result = build_planning_result()
    del planning_result[field]

    assert_reason_error(build_filtering_result(), planning_result, 400)


@pytest.mark.parametrize("field", ["profile_id", "dialogue_id"])
@pytest.mark.parametrize("bad_value", [0, -1, True, "25", None])
def test_标识必须为正整数(field, bad_value, assert_reason_error):
    planning_result = build_planning_result()
    planning_result[field] = bad_value

    assert_reason_error(build_filtering_result(), planning_result, 400)


@pytest.mark.parametrize("bad_dishes", [None, {}, "候选", [None]])
def test_候选分组必须为二维数组(bad_dishes, assert_reason_error):
    assert_reason_error(
        {"dishes": bad_dishes},
        build_planning_result(),
        400,
    )


@pytest.mark.parametrize("bad_name", [None, "", "  ", 1, True])
def test_候选菜名必须为非空字符串(bad_name, assert_reason_error):
    filtering_result = build_filtering_result()
    filtering_result["dishes"][0][0]["recipe_name"] = bad_name

    assert_reason_error(filtering_result, build_planning_result(), 400)


@pytest.mark.parametrize("bad_candidate", [None, [], "候选", 1, True])
def test_候选元素必须为对象(bad_candidate, assert_reason_error):
    filtering_result = build_filtering_result(dishes=[[bad_candidate]])

    assert_reason_error(filtering_result, build_planning_result(), 400)


def test_候选缺少菜名返回400(assert_reason_error):
    filtering_result = build_filtering_result(
        dishes=[[{"matched_tags": [], "matched_groups": []}]]
    )

    assert_reason_error(filtering_result, build_planning_result(), 400)


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("matched_tags", None),
        ("matched_tags", "晚餐"),
        ("matched_tags", ["晚餐", "晚餐"]),
        ("matched_tags", [""]),
        ("matched_tags", [1]),
        ("matched_groups", None),
        ("matched_groups", "餐次"),
        ("matched_groups", ["餐次", "餐次"]),
        ("matched_groups", ["  "]),
        ("matched_groups", [1]),
    ],
)
def test_被选中候选标签数组结构非法返回400(
    field,
    bad_value,
    assert_reason_error,
):
    filtering_result = build_filtering_result()
    filtering_result["dishes"][0][0][field] = bad_value

    assert_reason_error(filtering_result, build_planning_result(), 400)


@pytest.mark.parametrize("field", ["matched_tags", "matched_groups"])
def test_被选中候选缺少标签字段返回400(field, assert_reason_error):
    filtering_result = build_filtering_result()
    del filtering_result["dishes"][0][0][field]

    assert_reason_error(filtering_result, build_planning_result(), 400)


@pytest.mark.parametrize("bad_selected", [None, {}, "菜品", []])
def test_最终菜品必须为非空数组(bad_selected, assert_reason_error):
    planning_result = build_planning_result()
    planning_result["selected_dishes"] = bad_selected

    assert_reason_error(build_filtering_result(), planning_result, 400)


@pytest.mark.parametrize("bad_selected", [None, [], "菜品", 1, True])
def test_最终菜品元素必须为对象(bad_selected, assert_reason_error):
    planning_result = build_planning_result()
    planning_result["selected_dishes"] = [bad_selected]

    assert_reason_error(build_filtering_result(), planning_result, 400)


@pytest.mark.parametrize("field", ["dish_constraint_index", "recipe_name"])
def test_最终菜品缺少必需字段返回400(field, assert_reason_error):
    selected = build_selected_dish()
    del selected[field]
    planning_result = build_planning_result(selected_dishes=[selected])

    assert_reason_error(build_filtering_result(), planning_result, 400)


def test_最终菜品名称不得重复(assert_reason_error):
    planning_result = build_planning_result(
        selected_dishes=[
            build_selected_dish(recipe_name="白灼芥蓝"),
            build_selected_dish(recipe_name="白灼芥蓝"),
        ]
    )

    assert_reason_error(build_filtering_result(), planning_result, 400)


@pytest.mark.parametrize("bad_index", [-1, True, "0", None])
def test_菜品组索引必须为非负整数(bad_index, assert_reason_error):
    planning_result = build_planning_result(
        selected_dishes=[build_selected_dish(bad_index)]
    )

    assert_reason_error(build_filtering_result(), planning_result, 400)


@pytest.mark.parametrize("bad_name", [None, "", "  ", 1, True])
def test_最终菜名必须为非空字符串(bad_name, assert_reason_error):
    planning_result = build_planning_result(
        selected_dishes=[build_selected_dish(recipe_name=bad_name)]
    )

    assert_reason_error(build_filtering_result(), planning_result, 400)


@pytest.mark.parametrize("bad_score", [-1, 17, True, "9", None])
def test_营养总分必须为0到16整数(bad_score, assert_reason_error):
    planning_result = build_planning_result()
    planning_result["nutrition_score"] = bad_score

    assert_reason_error(build_filtering_result(), planning_result, 400)


def test_缺少任一计分营养项返回400(assert_reason_error):
    planning_result = build_planning_result()
    del planning_result["nutrient_grades"]["iron_mg"]

    assert_reason_error(build_filtering_result(), planning_result, 400)


@pytest.mark.parametrize("bad_grades", [None, [], "等级", 1, True])
def test_营养等级集合必须为对象(bad_grades, assert_reason_error):
    planning_result = build_planning_result()
    planning_result["nutrient_grades"] = bad_grades

    assert_reason_error(build_filtering_result(), planning_result, 400)


@pytest.mark.parametrize("bad_grade", [None, [], "等级", 1, True])
def test_单项营养等级必须为对象(bad_grade, assert_reason_error):
    planning_result = build_planning_result()
    planning_result["nutrient_grades"]["energy_kcal"] = bad_grade

    assert_reason_error(build_filtering_result(), planning_result, 400)


@pytest.mark.parametrize("field", ["actual_value", "grade", "score"])
def test_单项营养等级缺少消费字段返回400(field, assert_reason_error):
    planning_result = build_planning_result()
    del planning_result["nutrient_grades"]["energy_kcal"][field]

    assert_reason_error(build_filtering_result(), planning_result, 400)


@pytest.mark.parametrize(
    "grade_value",
    [
        build_grade(actual_value=Decimal("-0.01")),
        build_grade(actual_value=1),
        build_grade(grade="excellent", score=1),
        build_grade(grade="normal", score=2),
        build_grade(grade="bad", score=1),
        {"actual_value": Decimal("1"), "grade": "great", "score": 2},
        {"actual_value": Decimal("1"), "grade": "excellent"},
    ],
)
def test_营养等级结构范围或对应关系非法返回400(
    grade_value,
    assert_reason_error,
):
    planning_result = build_planning_result()
    planning_result["nutrient_grades"]["energy_kcal"] = grade_value

    assert_reason_error(build_filtering_result(), planning_result, 400)


@pytest.mark.parametrize(
    "bad_constraints",
    [None, "高血压", [""], [1], ["高血压", "高血压"]],
)
def test_已应用健康约束结构非法返回400(
    bad_constraints,
    assert_reason_error,
):
    planning_result = build_planning_result()
    planning_result["applied_health_constraints"] = bad_constraints

    assert_reason_error(build_filtering_result(), planning_result, 400)


def test_额外字段和额外营养项被忽略(invoke_build):
    filtering_result = build_filtering_result(extra_filtering={"任意": True})
    planning_result = build_planning_result(extra_planning={"任意": True})
    planning_result["nutrient_grades"]["custom_nutrient"] = {
        "任意": "无需校验"
    }

    result = invoke_build(filtering_result, planning_result)

    details = result["menu_reasons"][-1]["nutrient_details"]
    assert [item["nutrient"] for item in details] == [
        nutrient for nutrient, _, _ in NUTRIENTS
    ]
    assert "extra_filtering" not in result
    assert "extra_planning" not in result


def test_未选中候选的标签字段不校验也不解释(invoke_build):
    unselected = build_candidate("未选中的菜")
    unselected["matched_tags"] = object()
    unselected["matched_groups"] = object()
    filtering_result = build_filtering_result(
        dishes=[[unselected, build_candidate()]]
    )

    result = invoke_build(filtering_result, build_planning_result())

    assert [item["recipe_name"] for item in result["dish_recommendations"]] == [
        "白灼芥蓝"
    ]


def test_菜单规划携带的命中标签不读取也不比较(invoke_build):
    planning_result = build_planning_result()
    planning_result["selected_dishes"][0]["matched_tags"] = object()

    result = invoke_build(build_filtering_result(), planning_result)

    assert [
        reason["matched_tags"]
        for reason in result["dish_recommendations"][0]["reasons"]
    ] == [["晚餐"], ["清淡"], ["粤菜"]]
