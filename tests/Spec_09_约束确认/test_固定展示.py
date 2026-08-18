from __future__ import annotations

from .spec09_support import (
    build_dish,
    build_get_result,
    build_merged,
    needs_confirmation,
    resolved,
)


def test_全部约束按固定顺序展示(build_service):
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
                required_ingredients=[
                    {"kind": "ingredient", "value": "番茄"},
                    {"kind": "concept", "value": "面"},
                ],
            ),
            build_dish(dish_type="未指定"),
        ],
    )
    service, multi_turn, _ = build_service(resolved("晚餐"))
    multi_turn.get_result = build_get_result(merged)

    result = service.get_session(101)

    assert result["known_constraints"] == [
        {"path": "meal_period", "label": "餐次", "value": "晚餐", "source": "explicit"},
        {"path": "diner_count", "label": "人数", "value": "2人", "source": "explicit"},
        {
            "path": "total_dish_count",
            "label": "菜品数量",
            "value": "2道",
            "source": "derived",
        },
        {
            "path": "max_total_time_minutes",
            "label": "最长制作时间",
            "value": "45分钟以内",
            "source": "explicit",
        },
        {"path": "max_difficulty", "label": "难度", "value": "中等", "source": "explicit"},
        {
            "path": "available_ingredients",
            "label": "现有食材",
            "value": "番茄、鸡蛋",
            "source": "explicit",
        },
        {
            "path": "dishes[0].count",
            "label": "菜品组1数量",
            "value": "2道",
            "source": "explicit",
        },
        {
            "path": "dishes[0].dish_type",
            "label": "菜品组1类型",
            "value": "菜",
            "source": "explicit",
        },
        {
            "path": "dishes[0].taste_preferences",
            "label": "菜品组1口味",
            "value": "不甜、清淡、辣、不咸、酸",
            "source": "explicit",
        },
        {
            "path": "dishes[0].cuisines",
            "label": "菜品组1菜系",
            "value": "川湘菜、粤菜",
            "source": "explicit",
        },
        {
            "path": "dishes[0].effects",
            "label": "菜品组1功效",
            "value": "减脂",
            "source": "explicit",
        },
        {
            "path": "dishes[0].special_populations",
            "label": "菜品组1适用人群",
            "value": "上班族",
            "source": "explicit",
        },
        {
            "path": "dishes[0].required_ingredients",
            "label": "菜品组1必需食材",
            "value": "番茄、面",
            "source": "explicit",
        },
    ]


def test_可规划文案精确包含来源后缀(build_service):
    merged = build_merged()
    service, multi_turn, _ = build_service(
        resolved("午餐", source="current_time")
    )
    multi_turn.get_result = build_get_result(merged)

    result = service.get_session(101)

    assert result["message"] == (
        "已确定：\n"
        "- 餐次：午餐（根据当前时间）\n"
        "- 人数：1人（默认）\n"
        "- 菜品数量：1道（默认）\n"
        "可以开始规划。"
    )


def test_待确认文案仍展示其他已知约束(build_service):
    merged = build_merged(diner_count=2, total_dish_count=4)
    service, multi_turn, _ = build_service(
        needs_confirmation("outside_meal_window")
    )
    multi_turn.get_result = build_get_result(merged)

    result = service.get_session(101)

    assert result["known_constraints"] == [
        {"path": "diner_count", "label": "人数", "value": "2人", "source": "explicit"},
        {
            "path": "total_dish_count",
            "label": "菜品数量",
            "value": "4道",
            "source": "explicit",
        },
    ]
    assert result["message"] == (
        "已确定：\n"
        "- 人数：2人\n"
        "- 菜品数量：4道\n"
        "还需要确认：\n"
        "请确认这次要安排早餐、午餐还是晚餐？"
    )
