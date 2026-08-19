from __future__ import annotations

from .spec09_support import (
    build_dish,
    build_get_state,
    build_merged,
    needs_confirmation,
    resolved,
)


def test_全部非空约束按固定顺序和格式展示(build_service):
    merged = build_merged(
        meal_periods=["晚餐"],
        diner_count=2,
        max_total_time_minutes=45,
        max_difficulty="中等",
        available_ingredients=["番茄", "鸡蛋"],
        dishes=[
            build_dish(
                count=2,
                dish_type="菜",
                taste_preferences={
                    "is_sour": True,
                    "is_salty": False,
                    "is_spicy": True,
                    "is_light": True,
                    "is_sweet": False,
                },
                cuisines=["川湘菜", "粤菜"],
                effects=["减脂"],
                special_populations=["上班族"],
                required_ingredient_groups=[
                    {
                        "match": "all",
                        "items": [
                            {"kind": "ingredient", "value": "番茄"},
                            {"kind": "concept", "value": "面"},
                        ],
                    }
                ],
            )
        ],
    )
    service, multi_turn, _ = build_service(resolved("晚餐"))
    multi_turn.get_result = build_get_state(merged)

    result = service.get_session(101)

    assert [item["label"] for item in result["known_constraints"]] == [
        "餐次",
        "人数",
        "菜品数量",
        "最长制作时间",
        "难度",
        "现有食材",
        "菜品组1数量",
        "菜品组1类型",
        "菜品组1口味",
        "菜品组1菜系",
        "菜品组1功效",
        "菜品组1适用人群",
        "菜品组1所需食材",
    ]
    assert result["known_constraints"][8]["value"] == (
        "不甜、清淡、辣、不咸、酸"
    )
    assert result["known_constraints"][9]["value"] == "川湘菜、粤菜"
    assert all(
        set(item) == {"path", "label", "value", "source"}
        and all(isinstance(value, str) for value in item.values())
        for item in result["known_constraints"]
    )


def test_null空容器未指定与内部字段不展示(build_service):
    merged = build_merged(
        evidence={"diner_count": "两个人"},
        dishes=[build_dish()],
    )
    service, multi_turn, _ = build_service(resolved("晚餐"))
    multi_turn.get_result = build_get_state(merged)

    result = service.get_session(101)

    assert [item["path"] for item in result["known_constraints"]] == [
        "meal_period",
        "diner_count",
        "total_dish_count",
    ]


def test_可规划固定文案包含全部来源后缀(build_service):
    merged = build_merged(
        dishes=[build_dish(count=1, dish_type="主食")]
    )
    service, multi_turn, _ = build_service(
        resolved("午餐", source="current_time")
    )
    multi_turn.get_result = build_get_state(merged)

    result = service.get_session(101)

    assert result["message"] == (
        "已确定：\n"
        "- 餐次：午餐（根据当前时间）\n"
        "- 人数：1人（默认）\n"
        "- 菜品数量：1道（根据各菜品数量合计）\n"
        "- 菜品组1数量：1道\n"
        "- 菜品组1类型：主食\n"
        "可以开始规划。"
    )


def test_待确认固定文案仍展示其他已知约束(build_service):
    merged = build_merged(diner_count=2, total_dish_count=4)
    service, multi_turn, _ = build_service(
        needs_confirmation("outside_meal_window")
    )
    multi_turn.get_result = build_get_state(merged)

    result = service.get_session(101)

    assert result["confirmation"] == {
        "reason": "outside_meal_window",
        "options": ["早餐", "午餐", "晚餐"],
        "question": "请确认这次要安排早餐、午餐还是晚餐？",
    }
    assert result["message"] == (
        "已确定：\n"
        "- 人数：2人\n"
        "- 菜品数量：4道\n"
        "还需要确认：\n"
        "请确认这次要安排早餐、午餐还是晚餐？"
    )
