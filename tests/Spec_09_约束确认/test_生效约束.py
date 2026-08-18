from __future__ import annotations

from .spec09_support import (
    build_dish,
    build_get_result,
    build_merged,
    resolved,
)


def test_当前时间与默认值形成可规划上下文(build_service):
    merged = build_merged()
    service, multi_turn, _ = build_service(
        resolved("午餐", source="current_time")
    )
    multi_turn.get_result = build_get_result(merged)

    result = service.get_session(101)

    assert result["status"] == "ready_for_planning"
    assert result["planning_context"] == {
        "meal_period": "午餐",
        "meal_period_source": "current_time",
        "diner_count": 1,
        "diner_count_source": "default",
        "total_dish_count": 1,
        "total_dish_count_source": "default",
    }
    assert result["merged_constraints"] is merged


def test_明确三项优先于默认值(build_service):
    merged = build_merged(
        meal_periods=["晚餐"],
        diner_count=6,
        total_dish_count=8,
    )
    service, multi_turn, _ = build_service(resolved("晚餐"))
    multi_turn.get_result = build_get_result(merged)

    result = service.get_session(101)

    assert result["planning_context"] == {
        "meal_period": "晚餐",
        "meal_period_source": "explicit",
        "diner_count": 6,
        "diner_count_source": "explicit",
        "total_dish_count": 8,
        "total_dish_count_source": "explicit",
    }


def test_两菜一汤按分组数量合计(build_service):
    merged = build_merged(
        diner_count=2,
        dishes=[
            build_dish(count=2, dish_type="菜"),
            build_dish(count=1, dish_type="汤"),
        ],
    )
    service, multi_turn, _ = build_service(resolved("晚餐"))
    multi_turn.get_result = build_get_result(merged)

    context = service.get_session(101)["planning_context"]

    assert context["total_dish_count"] == 3
    assert context["total_dish_count_source"] == "dish_counts"


def test_默认菜数至少容纳两个未定量组(build_service):
    merged = build_merged(
        dishes=[
            build_dish(dish_type="主食"),
            build_dish(dish_type="小菜"),
        ]
    )
    service, multi_turn, _ = build_service(resolved("晚餐"))
    multi_turn.get_result = build_get_result(merged)

    context = service.get_session(101)["planning_context"]

    assert context["diner_count"] == 1
    assert context["total_dish_count"] == 2
    assert context["total_dish_count_source"] == "default"


def test_默认菜数至少容纳明确数量与未定量组(build_service):
    merged = build_merged(
        diner_count=2,
        dishes=[
            build_dish(count=3, dish_type="菜"),
            build_dish(dish_type="汤"),
        ],
    )
    service, multi_turn, _ = build_service(resolved("晚餐"))
    multi_turn.get_result = build_get_result(merged)

    context = service.get_session(101)["planning_context"]

    assert context["total_dish_count"] == 4


def test_四人按人数默认三道且不低于组最低数(build_service):
    merged = build_merged(
        diner_count=4,
        dishes=[
            build_dish(count=1, dish_type="汤"),
            build_dish(dish_type="菜"),
        ],
    )
    service, multi_turn, _ = build_service(resolved("晚餐"))
    multi_turn.get_result = build_get_result(merged)

    context = service.get_session(101)["planning_context"]

    assert context["total_dish_count"] == 3


def test_未明确餐次时每次读取重新判断(build_service):
    merged = build_merged()
    service, multi_turn, _ = build_service(
        resolved("午餐", source="current_time"),
        resolved("晚餐", source="current_time"),
    )
    multi_turn.get_result = build_get_result(merged)

    first = service.get_session(101)
    second = service.get_session(101)

    assert first["planning_context"]["meal_period"] == "午餐"
    assert second["planning_context"]["meal_period"] == "晚餐"


def test_明确餐次每次都按用户值解析(build_service):
    merged = build_merged(meal_periods=["早餐"])
    service, multi_turn, meal_period = build_service(
        resolved("早餐"),
        resolved("早餐"),
    )
    multi_turn.get_result = build_get_result(merged)

    first = service.get_session(101)
    second = service.get_session(101)

    assert first["planning_context"]["meal_period"] == "早餐"
    assert second["planning_context"]["meal_period"] == "早餐"
    assert meal_period.inputs == [["早餐"], ["早餐"]]
