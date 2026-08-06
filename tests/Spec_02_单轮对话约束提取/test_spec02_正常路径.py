from __future__ import annotations

from typing import Any

import pytest

from spec02_support import (
    FakeLLMClient,
    build_empty_dish,
    build_empty_result,
    ingredient_session,
    invoke_extract,
    production_contract,
)


def build_dish(**overrides: Any) -> dict[str, Any]:
    dish = build_empty_dish()
    dish.update(overrides)
    return dish


def build_result(dialogue_id: int, **overrides: Any) -> dict[str, Any]:
    result = build_empty_result(dialogue_id)
    result.update(overrides)
    return result


GOLDEN_CASES = [
    (
        {"id": 1, "turn_count": 1, "user_messages": ["今晚吃啥比较好？"]},
        build_result(
            1,
            meal_periods=["晚餐"],
            evidence={"meal_periods[0]": "今晚"},
        ),
    ),
    (
        {"id": 2, "turn_count": 1, "user_messages": ["帮我想个简单点的早餐。"]},
        build_result(
            2,
            meal_periods=["早餐"],
            evidence={"meal_periods[0]": "早餐"},
        ),
    ),
    (
        {
            "id": 3,
            "turn_count": 1,
            "user_messages": ["中午想吃点清爽的，有没有那种适合夏天的搭配？"],
        },
        build_result(
            3,
            meal_periods=["午餐"],
            dishes=[build_dish(taste_preferences={"is_light": True})],
            evidence={
                "meal_periods[0]": "中午",
                "dishes[0].taste_preferences.is_light": "清爽",
            },
        ),
    ),
    (
        {
            "id": 4,
            "turn_count": 1,
            "user_messages": ["晚上两个人吃，最近胃口不太好"],
        },
        build_result(
            4,
            meal_periods=["晚餐"],
            diner_count=2,
            dishes=[build_dish(effects=["养胃健胃消食"])],
            evidence={
                "meal_periods[0]": "晚上",
                "diner_count": "两个人",
                "dishes[0].effects[0]": "胃口不太好",
            },
        ),
    ),
    (
        {
            "id": 5,
            "turn_count": 1,
            "user_messages": ["帮我想个带去公司的午饭吧"],
        },
        build_result(
            5,
            meal_periods=["午餐"],
            dishes=[build_dish(special_populations=["上班族"])],
            evidence={
                "meal_periods[0]": "午饭",
                "dishes[0].special_populations[0]": "公司",
            },
        ),
    ),
    (
        {
            "id": 6,
            "turn_count": 1,
            "user_messages": ["我今天下班会比较晚，想做个半小时内能搞定的晚饭。"],
        },
        build_result(
            6,
            meal_periods=["晚餐"],
            max_total_time_minutes=30,
            dishes=[build_dish(special_populations=["上班族"])],
            evidence={
                "meal_periods[0]": "晚饭",
                "max_total_time_minutes": "半小时内",
                "dishes[0].special_populations[0]": "下班",
            },
        ),
    ),
    (
        {
            "id": 7,
            "turn_count": 1,
            "user_messages": ["家里现在就剩番茄、鸡蛋和土豆了，这顿饭还能怎么弄？要能当正餐。"],
        },
        build_result(
            7,
            available_ingredients=["番茄", "鸡蛋", "土豆"],
            evidence={
                "available_ingredients[0]": "番茄",
                "available_ingredients[1]": "鸡蛋",
                "available_ingredients[2]": "土豆",
            },
        ),
    ),
    (
        {
            "id": 8,
            "turn_count": 1,
            "user_messages": ["我今晚有点想吃面，再帮我配个别太抢味的小菜。"],
        },
        build_result(
            8,
            meal_periods=["晚餐"],
            dishes=[
                build_dish(
                    count=1,
                    dish_type="主食",
                    required_ingredients=[
                        {"kind": "concept", "value": "面"}
                    ],
                ),
                build_dish(
                    count=1,
                    dish_type="小菜",
                    taste_preferences={"is_light": True},
                ),
            ],
            evidence={
                "meal_periods[0]": "今晚",
                "dishes[0].count": "面",
                "dishes[0].dish_type": "面",
                "dishes[0].required_ingredients[0].value": "面",
                "dishes[1].count": "配个",
                "dishes[1].dish_type": "小菜",
                "dishes[1].taste_preferences.is_light": "别太抢味",
            },
        ),
    ),
    (
        {
            "id": 9,
            "turn_count": 1,
            "user_messages": ["周末想在家吃得有点仪式感，但我又不想做太复杂。"],
        },
        build_result(
            9,
            dishes=[build_dish(cuisines=["西餐风味"])],
            evidence={"dishes[0].cuisines[0]": "仪式感"},
        ),
    ),
    (
        {
            "id": 10,
            "turn_count": 1,
            "user_messages": ["晚上有点饿，想吃个热乎点的夜宵"],
        },
        build_result(
            10,
            meal_periods=["晚餐"],
            evidence={"meal_periods[0]": "夜宵"},
        ),
    ),
    (
        {
            "id": 11,
            "turn_count": 1,
            "user_messages": ["想做顿一家四口吃的晚饭"],
        },
        build_result(
            11,
            meal_periods=["晚餐"],
            diner_count=4,
            evidence={
                "meal_periods[0]": "晚饭",
                "diner_count": "一家四口",
            },
        ),
    ),
    (
        {
            "id": 12,
            "turn_count": 1,
            "user_messages": ["想做个四菜一汤，营养均衡一点的"],
        },
        build_result(
            12,
            dishes=[
                build_dish(count=4, dish_type="菜"),
                build_dish(count=1, dish_type="汤"),
            ],
            evidence={
                "dishes[0].count": "四菜",
                "dishes[0].dish_type": "四菜",
                "dishes[1].count": "一汤",
                "dishes[1].dish_type": "一汤",
            },
        ),
    ),
    (
        {
            "id": 13,
            "turn_count": 1,
            "user_messages": ["今天状态不太好，想吃点暖胃的。"],
        },
        build_result(
            13,
            dishes=[build_dish(effects=["养胃健胃消食"])],
            evidence={"dishes[0].effects[0]": "暖胃"},
        ),
    ),
    (
        {
            "id": 14,
            "turn_count": 1,
            "user_messages": ["想做个四菜一汤，营养均衡一点的，小孩不吃辣，老人牙口不好"],
        },
        build_result(
            14,
            dishes=[
                build_dish(
                    count=4,
                    dish_type="菜",
                    taste_preferences={"is_spicy": False},
                    special_populations=["儿童", "老人"],
                ),
                build_dish(
                    count=1,
                    dish_type="汤",
                    taste_preferences={"is_spicy": False},
                    special_populations=["儿童", "老人"],
                ),
            ],
            evidence={
                "dishes[0].count": "四菜",
                "dishes[0].dish_type": "四菜",
                "dishes[0].taste_preferences.is_spicy": "不吃辣",
                "dishes[0].special_populations[0]": "小孩",
                "dishes[0].special_populations[1]": "老人",
                "dishes[1].count": "一汤",
                "dishes[1].dish_type": "一汤",
                "dishes[1].taste_preferences.is_spicy": "不吃辣",
                "dishes[1].special_populations[0]": "小孩",
                "dishes[1].special_populations[1]": "老人",
            },
        ),
    ),
]


@pytest.mark.parametrize(
    ("dialogue", "expected"),
    GOLDEN_CASES,
    ids=[f"单轮用例_{index}" for index in range(1, 15)],
)
def test_提取现有单轮金标准(dialogue, expected, invoke_extract):
    llm_client = FakeLLMClient(expected)

    result = invoke_extract(dialogue, llm_client)

    assert result == expected
    assert llm_client.call_count == 1
    assert dialogue["user_messages"][0] in llm_client.prompts[0]
