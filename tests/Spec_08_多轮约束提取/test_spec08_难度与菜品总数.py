from __future__ import annotations

import copy

import pytest

from backend.core.multi_turn_contract import MULTI_TURN_OUTPUT_SCHEMA
from backend.services.multi_turn_constraints import _build_prompt

from .spec08_support import (
    build_dish,
    build_dish_action,
    build_top_action,
    build_turn_result,
)


def test_多轮输出契约包含两个新增必填字段():
    required = set(MULTI_TURN_OUTPUT_SCHEMA["required"])
    properties = MULTI_TURN_OUTPUT_SCHEMA["properties"]

    assert {"total_dish_count", "max_difficulty"} <= required
    assert properties["total_dish_count"] == {
        "anyOf": [
            {"type": "integer", "minimum": 1},
            {"type": "null"},
        ]
    }
    assert properties["max_difficulty"] == {
        "anyOf": [
            {"type": "string", "enum": ["简单", "中等"]},
            {"type": "null"},
        ]
    }


def test_Prompt明确包含难度总数和口味拆组规则():
    prompt = _build_prompt(1, "家常一点，四个菜", None, {"蔬菜"})

    assert "max_difficulty" in prompt
    assert "total_dish_count" in prompt
    assert "家常一点" in prompt and "简单" in prompt
    assert "太麻烦" in prompt and "中等" in prompt
    assert "一个人" in prompt and "count" in prompt and "null" in prompt
    assert "复杂" in prompt and "忽略" in prompt
    assert "共用食材" in prompt and "不想分开做两套" in prompt


def test_首轮可保存菜品总数和难度上限(start_session):
    service, llm_client, session_id = start_session()
    llm_client.response = build_turn_result(
        session_id,
        meal_periods=["晚餐"],
        total_dish_count=4,
        max_difficulty="简单",
        evidence={
            "meal_periods[0]": "晚饭",
            "total_dish_count": "四个菜",
            "max_difficulty": "家常一点",
        },
    )

    result = service.submit_turn(
        session_id,
        "晚饭做四个菜，家常一点",
    )

    merged = result["merged_constraints"]
    assert merged["total_dish_count"] == 4
    assert merged["max_difficulty"] == "简单"
    assert merged["dishes"][0]["count"] is None


def test_口味冲突拆成两个未分配数量的菜品组(start_session):
    service, llm_client, session_id = start_session()
    first = build_turn_result(
        session_id,
        meal_periods=["晚餐"],
        diner_count=2,
        total_dish_count=4,
        evidence={
            "meal_periods[0]": "晚饭",
            "diner_count": "两个人",
            "total_dish_count": "四个菜",
        },
    )
    split = build_turn_result(
        session_id,
        meal_periods=["晚餐"],
        diner_count=2,
        total_dish_count=4,
        dishes=[
            build_dish(taste_preferences={"is_spicy": True}),
            build_dish(taste_preferences={"is_spicy": False}),
        ],
        evidence={
            "dishes[0].taste_preferences.is_spicy": "一个人想吃辣",
            "dishes[1].taste_preferences.is_spicy": "一个人不碰辣",
        },
        change_actions=[
            build_dish_action(0, "replace", "一个人想吃辣"),
            build_dish_action(None, "add", "一个人不碰辣"),
        ],
    )
    llm_client.responses = [first, split]

    service.submit_turn(session_id, "两个人晚饭，做四个菜")
    result = service.submit_turn(
        session_id,
        "一个人想吃辣，一个人不碰辣",
    )

    merged = result["merged_constraints"]
    assert merged["total_dish_count"] == 4
    assert len(merged["dishes"]) == 2
    assert [dish["count"] for dish in merged["dishes"]] == [None, None]
    assert "dishes[0].count" not in merged["evidence"]
    assert "dishes[1].count" not in merged["evidence"]


def test_未指定组的追加只增加整桌总数(start_session):
    service, llm_client, session_id = start_session()
    dishes = [
        build_dish(taste_preferences={"is_spicy": True}),
        build_dish(taste_preferences={"is_spicy": False}),
    ]
    first = build_turn_result(
        session_id,
        total_dish_count=4,
        dishes=dishes,
        evidence={
            "total_dish_count": "四个菜",
            "dishes[0].taste_preferences.is_spicy": "吃辣",
            "dishes[1].taste_preferences.is_spicy": "不辣",
        },
    )
    second = build_turn_result(
        session_id,
        total_dish_count=5,
        dishes=copy.deepcopy(dishes),
        evidence={"total_dish_count": "再加一个菜"},
        change_actions=[
            build_top_action(
                "total_dish_count",
                "add",
                "再加一个菜",
            )
        ],
    )
    llm_client.responses = [first, second]

    service.submit_turn(session_id, "四个菜，一个吃辣一个不辣")
    result = service.submit_turn(session_id, "再加一个菜")

    merged = result["merged_constraints"]
    assert merged["total_dish_count"] == 5
    assert [dish["count"] for dish in merged["dishes"]] == [None, None]


def test_整桌总数可以解除为null(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_turn_result(
            session_id,
            total_dish_count=4,
            evidence={"total_dish_count": "四个菜"},
        ),
        build_turn_result(
            session_id,
            total_dish_count=None,
            change_actions=[
                build_top_action(
                    "total_dish_count",
                    "remove",
                    "菜品数量不限",
                )
            ],
        ),
    ]

    service.submit_turn(session_id, "四个菜")
    result = service.submit_turn(session_id, "菜品数量不限")

    assert result["merged_constraints"]["total_dish_count"] is None


def test_旧总数为null时相对追加返回502(
    start_session,
    assert_multi_turn_error,
):
    service, llm_client, session_id = start_session()
    first = build_turn_result(session_id)
    invalid = build_turn_result(
        session_id,
        total_dish_count=1,
        evidence={"total_dish_count": "再加一道"},
        change_actions=[
            build_top_action("total_dish_count", "add", "再加一道")
        ],
    )
    llm_client.responses = [first, invalid, copy.deepcopy(invalid)]

    service.submit_turn(session_id, "随便做点")
    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "再加一道"),
        502,
    )


def test_旧组数量为null时相对追加返回502(
    start_session,
    assert_multi_turn_error,
):
    service, llm_client, session_id = start_session()
    first = build_turn_result(
        session_id,
        dishes=[build_dish(dish_type="汤")],
        evidence={"dishes[0].dish_type": "汤"},
    )
    invalid = build_turn_result(
        session_id,
        dishes=[build_dish(count=1, dish_type="汤")],
        evidence={"dishes[0].count": "汤再加一道"},
        change_actions=[build_dish_action(0, "add", "汤再加一道")],
    )
    llm_client.responses = [first, invalid, copy.deepcopy(invalid)]

    service.submit_turn(session_id, "做个汤")
    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "汤再加一道"),
        502,
    )


def test_指定组追加同时增加总数和组内数量(start_session):
    service, llm_client, session_id = start_session()
    first = build_turn_result(
        session_id,
        total_dish_count=3,
        dishes=[
            build_dish(count=2, dish_type="菜"),
            build_dish(count=1, dish_type="汤"),
        ],
        evidence={
            "total_dish_count": "共三道",
            "dishes[0].count": "两菜",
            "dishes[0].dish_type": "两菜",
            "dishes[1].count": "一汤",
            "dishes[1].dish_type": "一汤",
        },
    )
    second = build_turn_result(
        session_id,
        total_dish_count=4,
        dishes=[
            build_dish(count=2, dish_type="菜"),
            build_dish(count=2, dish_type="汤"),
        ],
        evidence={
            "total_dish_count": "汤再加一道",
            "dishes[1].count": "汤再加一道",
        },
        change_actions=[
            build_top_action("total_dish_count", "add", "汤再加一道"),
            build_dish_action(1, "add", "汤再加一道"),
        ],
    )
    llm_client.responses = [first, second]

    service.submit_turn(session_id, "共三道，两菜一汤")
    result = service.submit_turn(session_id, "汤再加一道")

    assert result["merged_constraints"]["total_dish_count"] == 4
    assert result["merged_constraints"]["dishes"][1]["count"] == 2


def test_难度上限只允许替换和删除(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_turn_result(
            session_id,
            max_difficulty="中等",
            evidence={"max_difficulty": "别太复杂"},
        ),
        build_turn_result(
            session_id,
            max_difficulty="简单",
            evidence={"max_difficulty": "家常一点"},
            change_actions=[
                build_top_action(
                    "max_difficulty",
                    "replace",
                    "家常一点",
                )
            ],
        ),
        build_turn_result(
            session_id,
            max_difficulty=None,
            change_actions=[
                build_top_action(
                    "max_difficulty",
                    "remove",
                    "难度不限",
                )
            ],
        ),
    ]

    service.submit_turn(session_id, "别太复杂")
    replaced = service.submit_turn(session_id, "家常一点")
    removed = service.submit_turn(session_id, "难度不限")

    assert replaced["merged_constraints"]["max_difficulty"] == "简单"
    assert removed["merged_constraints"]["max_difficulty"] is None


def test_难度上限add返回502(
    start_session,
    assert_multi_turn_error,
):
    service, llm_client, session_id = start_session()
    first = build_turn_result(
        session_id,
        max_difficulty="中等",
        evidence={"max_difficulty": "别太复杂"},
    )
    invalid = build_turn_result(
        session_id,
        max_difficulty="简单",
        evidence={"max_difficulty": "再简单一点"},
        change_actions=[
            build_top_action(
                "max_difficulty",
                "add",
                "再简单一点",
            )
        ],
    )
    llm_client.responses = [first, invalid, copy.deepcopy(invalid)]

    service.submit_turn(session_id, "别太复杂")
    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "再简单一点"),
        502,
    )


@pytest.mark.parametrize(
    ("total_dish_count", "dishes", "message", "evidence"),
    [
        (
            1,
            [
                build_dish(taste_preferences={"is_spicy": True}),
                build_dish(taste_preferences={"is_spicy": False}),
            ],
            "只要一道，一个口味辣，一个口味不辣",
            {
                "total_dish_count": "只要一道",
                "dishes[0].taste_preferences.is_spicy": "口味辣",
                "dishes[1].taste_preferences.is_spicy": "口味不辣",
            },
        ),
        (
            4,
            [build_dish(count=1), build_dish(count=1, dish_type="汤")],
            "总共四道，一道菜一道汤",
            {
                "total_dish_count": "总共四道",
                "dishes[0].count": "一道菜",
                "dishes[1].count": "一道汤",
                "dishes[1].dish_type": "汤",
            },
        ),
    ],
)
def test_菜品总数与菜品组数量不一致时返回502(
    total_dish_count,
    dishes,
    message,
    evidence,
    start_session,
    assert_multi_turn_error,
):
    service, llm_client, session_id = start_session()
    invalid = build_turn_result(
        session_id,
        total_dish_count=total_dish_count,
        dishes=dishes,
        evidence=evidence,
    )
    llm_client.responses = [invalid, copy.deepcopy(invalid)]

    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, message),
        502,
    )
