from __future__ import annotations

from .spec09_support import (
    build_dish,
    build_get_state,
    build_merged,
    resolved,
)


def test_用户明确三项时全部采用明确值(build_service):
    merged = build_merged(
        meal_periods=["晚餐"],
        diner_count=6,
        total_dish_count=8,
    )
    service, multi_turn, _ = build_service(resolved("晚餐"))
    multi_turn.get_result = build_get_state(merged)

    assert service.get_session(101)["planning_context"] == {
        "meal_period": "晚餐",
        "meal_period_source": "explicit",
        "diner_count": 6,
        "diner_count_source": "explicit",
        "total_dish_count": 8,
        "total_dish_count_source": "explicit",
    }


def test_未说明人数时默认为一人(build_service):
    merged = build_merged()
    service, multi_turn, _ = build_service(
        resolved("午餐", source="current_time")
    )
    multi_turn.get_result = build_get_state(merged)

    context = service.get_session(101)["planning_context"]

    assert context["diner_count"] == 1
    assert context["diner_count_source"] == "default"


def test_两菜一汤按分组数量合计为三道(build_service):
    merged = build_merged(
        dishes=[
            build_dish(count=2, dish_type="菜"),
            build_dish(count=1, dish_type="汤"),
        ]
    )
    service, multi_turn, _ = build_service(resolved("晚餐"))
    multi_turn.get_result = build_get_state(merged)

    context = service.get_session(101)["planning_context"]

    assert context["total_dish_count"] == 3
    assert context["total_dish_count_source"] == "dish_counts"


def test_一人两个未定量组时默认两道(build_service):
    merged = build_merged(
        dishes=[
            build_dish(dish_type="主食"),
            build_dish(dish_type="小菜"),
        ]
    )
    service, multi_turn, _ = build_service(resolved("晚餐"))
    multi_turn.get_result = build_get_state(merged)

    assert service.get_session(101)["planning_context"][
        "total_dish_count"
    ] == 2


def test_两人三道主菜加未定量汤时默认四道(build_service):
    merged = build_merged(
        diner_count=2,
        dishes=[
            build_dish(count=3, dish_type="菜"),
            build_dish(dish_type="汤"),
        ],
    )
    service, multi_turn, _ = build_service(resolved("晚餐"))
    multi_turn.get_result = build_get_state(merged)

    assert service.get_session(101)["planning_context"][
        "total_dish_count"
    ] == 4


def test_四人默认三道且不低于分组最低数(build_service):
    merged = build_merged(
        diner_count=4,
        dishes=[
            build_dish(count=1, dish_type="汤"),
            build_dish(dish_type="菜"),
        ],
    )
    service, multi_turn, _ = build_service(resolved("晚餐"))
    multi_turn.get_result = build_get_state(merged)

    assert service.get_session(101)["planning_context"][
        "total_dish_count"
    ] == 3


def test_未明确餐次时每次读取都按当前时间重新判断(build_service):
    merged = build_merged()
    service, multi_turn, _ = build_service(
        resolved("午餐", source="current_time"),
        resolved("晚餐", source="current_time"),
    )
    multi_turn.get_result = build_get_state(merged)

    first = service.get_session(101)
    second = service.get_session(101)

    assert first["planning_context"]["meal_period"] == "午餐"
    assert second["planning_context"]["meal_period"] == "晚餐"


def test_用户明确餐次后不受时间变化影响(build_service):
    merged = build_merged(meal_periods=["早餐"])
    service, multi_turn, meal_period = build_service(
        resolved("早餐"),
        resolved("早餐"),
    )
    multi_turn.get_result = build_get_state(merged)

    first = service.get_session(101)
    second = service.get_session(101)

    assert first["planning_context"]["meal_period"] == "早餐"
    assert second["planning_context"]["meal_period"] == "早餐"
    assert meal_period.calls == [["早餐"], ["早餐"]]
