from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from .spec11_support import (
    build_dish,
    build_dish_action,
    build_top_action,
    build_turn_result,
)


def test_同一Dish多个字段变化合并为一条replace(start_session):
    service, llm_client, session_id = start_session()
    first = build_turn_result(
        session_id,
        meal_periods=["晚餐"],
        evidence={"meal_periods[0]": "晚饭"},
    )
    second = build_turn_result(
        session_id,
        meal_periods=["晚餐"],
        dishes=[
            build_dish(
                taste_preferences={"is_spicy": False, "is_light": True}
            )
        ],
        evidence={
            "dishes[0].taste_preferences.is_spicy": "别做辣的",
            "dishes[0].taste_preferences.is_light": "清淡一点",
        },
        change_actions=[
            build_dish_action(
                0,
                "replace",
                "别做辣的，口味清淡一点",
            )
        ],
    )
    llm_client.responses = [first, second]

    service.submit_turn(session_id, "帮我想顿晚饭")
    result = service.submit_turn(
        session_id,
        "别做辣的，口味清淡一点",
    )

    assert result["merged_constraints"]["dishes"][0][
        "taste_preferences"
    ] == {"is_spicy": False, "is_light": True}


def test_首轮change_actions非空返回502(
    start_session,
    assert_dialogue_error,
):
    service, llm_client, session_id = start_session()
    invalid = build_turn_result(
        session_id,
        diner_count=2,
        evidence={"diner_count": "两个人"},
        change_actions=[
            build_top_action("diner_count", "replace", "两个人")
        ],
    )
    llm_client.responses = [invalid, copy.deepcopy(invalid)]

    assert_dialogue_error(
        lambda: service.submit_turn(session_id, "两个人"),
        502,
    )


def test_标量add_replace_remove按规则重放(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_turn_result(
            session_id,
            diner_count=2,
            evidence={"diner_count": "两个人"},
        ),
        build_turn_result(
            session_id,
            diner_count=3,
            evidence={"diner_count": "再加一个人"},
            change_actions=[
                build_top_action("diner_count", "add", "再加一个人")
            ],
        ),
        build_turn_result(
            session_id,
            diner_count=4,
            evidence={"diner_count": "改成四个人"},
            change_actions=[
                build_top_action("diner_count", "replace", "改成四个人")
            ],
        ),
        build_turn_result(
            session_id,
            diner_count=None,
            change_actions=[
                build_top_action("diner_count", "remove", "人数不限")
            ],
        ),
    ]

    service.submit_turn(session_id, "两个人")
    added = service.submit_turn(session_id, "再加一个人")
    replaced = service.submit_turn(session_id, "改成四个人")
    removed = service.submit_turn(session_id, "人数不限")

    assert added["merged_constraints"]["diner_count"] == 3
    assert replaced["merged_constraints"]["diner_count"] == 4
    assert removed["merged_constraints"]["diner_count"] is None


def test_数组add和remove保持有序子集(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_turn_result(
            session_id,
            meal_periods=["晚餐"],
            evidence={"meal_periods[0]": "晚餐"},
        ),
        build_turn_result(
            session_id,
            meal_periods=["晚餐", "午餐"],
            evidence={"meal_periods[1]": "午餐"},
            change_actions=[
                build_top_action("meal_periods", "add", "午餐")
            ],
        ),
        build_turn_result(
            session_id,
            meal_periods=["午餐"],
            evidence={},
            change_actions=[
                build_top_action("meal_periods", "remove", "晚餐不要了")
            ],
        ),
    ]

    service.submit_turn(session_id, "晚餐")
    added = service.submit_turn(session_id, "午餐也要")
    removed = service.submit_turn(session_id, "晚餐不要了")

    assert added["merged_constraints"]["meal_periods"] == ["晚餐", "午餐"]
    assert removed["merged_constraints"]["meal_periods"] == ["午餐"]


def test_Dish新增替换删除均可重放(start_session):
    service, llm_client, session_id = start_session()
    base_dish = build_dish(dish_type="菜")
    soup = build_dish(dish_type="汤")
    light_dish = build_dish(
        dish_type="菜",
        taste_preferences={"is_light": True},
    )
    llm_client.responses = [
        build_turn_result(
            session_id,
            dishes=[base_dish],
            evidence={"dishes[0].dish_type": "菜"},
        ),
        build_turn_result(
            session_id,
            dishes=[base_dish, soup],
            evidence={"dishes[1].dish_type": "汤"},
            change_actions=[build_dish_action(None, "add", "加个汤")],
        ),
        build_turn_result(
            session_id,
            dishes=[light_dish, soup],
            evidence={
                "dishes[0].dish_type": "菜",
                "dishes[0].taste_preferences.is_light": "清淡点",
            },
            change_actions=[
                build_dish_action(0, "replace", "菜清淡点")
            ],
        ),
        build_turn_result(
            session_id,
            dishes=[light_dish],
            evidence={},
            change_actions=[build_dish_action(1, "remove", "汤不要了")],
        ),
    ]

    service.submit_turn(session_id, "做个菜")
    service.submit_turn(session_id, "加个汤")
    service.submit_turn(session_id, "菜清淡点")
    removed = service.submit_turn(session_id, "汤不要了")

    assert removed["merged_constraints"]["dishes"] == [light_dish]


def test_max_difficulty不允许add(start_session, assert_dialogue_error):
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
            build_top_action("max_difficulty", "add", "再简单一点")
        ],
    )
    llm_client.responses = [first, invalid, copy.deepcopy(invalid)]

    service.submit_turn(session_id, "别太复杂")
    assert_dialogue_error(
        lambda: service.submit_turn(session_id, "再简单一点"),
        502,
    )


def test_旧标量为null时add返回502(start_session, assert_dialogue_error):
    service, llm_client, session_id = start_session()
    first = build_turn_result(session_id)
    invalid = build_turn_result(
        session_id,
        diner_count=1,
        evidence={"diner_count": "再加一个人"},
        change_actions=[
            build_top_action("diner_count", "add", "再加一个人")
        ],
    )
    llm_client.responses = [first, invalid, copy.deepcopy(invalid)]

    service.submit_turn(session_id, "随便")
    assert_dialogue_error(
        lambda: service.submit_turn(session_id, "再加一个人"),
        502,
    )


def test_数组add破坏旧顺序返回502(start_session, assert_dialogue_error):
    service, llm_client, session_id = start_session()
    first = build_turn_result(
        session_id,
        available_ingredients=["番茄", "鸡蛋"],
        evidence={
            "available_ingredients[0]": "番茄",
            "available_ingredients[1]": "鸡蛋",
        },
    )
    invalid = build_turn_result(
        session_id,
        available_ingredients=["鸡蛋", "番茄", "土豆"],
        evidence={"available_ingredients[2]": "土豆"},
        change_actions=[
            build_top_action("available_ingredients", "add", "还有土豆")
        ],
    )
    llm_client.responses = [first, invalid, copy.deepcopy(invalid)]

    service.submit_turn(session_id, "家里有番茄和鸡蛋")
    assert_dialogue_error(
        lambda: service.submit_turn(session_id, "还有土豆"),
        502,
    )


def test_已有Dish的add只能增加非空count(
    start_session,
    assert_dialogue_error,
):
    service, llm_client, session_id = start_session()
    first = build_turn_result(
        session_id,
        dishes=[build_dish(dish_type="菜")],
        evidence={"dishes[0].dish_type": "菜"},
    )
    invalid = build_turn_result(
        session_id,
        dishes=[build_dish(dish_type="菜", cuisines=["粤菜"])],
        evidence={"dishes[0].cuisines[0]": "粤菜"},
        change_actions=[build_dish_action(0, "add", "加粤菜要求")],
    )
    llm_client.responses = [first, invalid, copy.deepcopy(invalid)]

    service.submit_turn(session_id, "做个菜")
    assert_dialogue_error(
        lambda: service.submit_turn(session_id, "加粤菜要求"),
        502,
    )


def test_同一目标重复声明返回502(start_session, assert_dialogue_error):
    service, llm_client, session_id = start_session()
    first = build_turn_result(session_id)
    invalid = build_turn_result(
        session_id,
        diner_count=3,
        evidence={"diner_count": "改成三个人"},
        change_actions=[
            build_top_action("diner_count", "replace", "改成三个人"),
            build_top_action("diner_count", "replace", "三个人"),
        ],
    )
    llm_client.responses = [first, invalid, copy.deepcopy(invalid)]

    service.submit_turn(session_id, "先随便看看")
    assert_dialogue_error(
        lambda: service.submit_turn(session_id, "改成三个人"),
        502,
    )


def test_未声明改动和声明重放不一致均返回502(
    start_session,
    assert_dialogue_error,
):
    service, llm_client, session_id = start_session()
    first = build_turn_result(
        session_id,
        diner_count=2,
        evidence={"diner_count": "两个人"},
    )
    undeclared = build_turn_result(
        session_id,
        diner_count=3,
        evidence={"diner_count": "三个人"},
    )
    llm_client.responses = [
        first,
        undeclared,
        copy.deepcopy(undeclared),
    ]
    service.submit_turn(session_id, "两个人")
    assert_dialogue_error(
        lambda: service.submit_turn(session_id, "三个人"),
        502,
    )


def test_证据必须精确覆盖并命中当前原文(
    start_session,
    assert_dialogue_error,
):
    service, llm_client, session_id = start_session()
    missing = build_turn_result(session_id, diner_count=2)
    llm_client.responses = [missing, copy.deepcopy(missing)]

    assert_dialogue_error(
        lambda: service.submit_turn(session_id, "两个人"),
        502,
    )


def test_重试Prompt包含首次具体错误且失败不落库(
    start_session,
    assert_dialogue_error,
    db_session,
    production_contract,
):
    service, llm_client, session_id = start_session()
    invalid = build_turn_result(session_id, diner_count=2)
    llm_client.responses = [invalid, copy.deepcopy(invalid)]

    assert_dialogue_error(
        lambda: service.submit_turn(session_id, "两个人"),
        502,
    )

    assert llm_client.call_count == 2
    assert "evidence" in llm_client.prompts[1]
    assert llm_client.prompts[1] != llm_client.prompts[0]
    turns = db_session.execute(
        select(production_contract.DialogueTurn).where(
            production_contract.DialogueTurn.session_id == session_id
        )
    ).scalars().all()
    assert turns == []
    row = db_session.get(production_contract.DialogueSession, session_id)
    assert row.merged_constraints is None


def test_成功轮次递增且失败轮次不占编号(
    start_session,
    assert_dialogue_error,
):
    service, llm_client, session_id = start_session()
    empty = build_turn_result(session_id)
    invalid = build_turn_result(session_id, diner_count=2)
    valid_second = build_turn_result(session_id)
    llm_client.responses = [
        empty,
        invalid,
        copy.deepcopy(invalid),
        valid_second,
    ]

    first = service.submit_turn(session_id, "随便")
    assert_dialogue_error(
        lambda: service.submit_turn(session_id, "两个人"),
        502,
    )
    second = service.submit_turn(session_id, "还是随便")

    assert first["turn_number"] == 1
    assert second["turn_number"] == 2


def test_并发提交由行锁和唯一约束串行化(start_session):
    service, llm_client, session_id = start_session()
    llm_client.response = build_turn_result(session_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda message: service.submit_turn(session_id, message),
                ["第一条", "第二条"],
            )
        )

    assert sorted(result["turn_number"] for result in results) == [1, 2]
