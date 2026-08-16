from __future__ import annotations

from .spec08_support import (
    build_dish,
    build_dish_action,
    build_first_dinner_for_two,
    build_inherited_dinner_for_two,
    build_top_action,
    build_turn_result,
)


def test_标量增_再加一个人(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_first_dinner_for_two(session_id),
        build_inherited_dinner_for_two(
            session_id,
            diner_count=3,
            evidence={"diner_count": "再加一个人"},
            change_actions=[
                build_top_action("diner_count", "add", "再加一个人")
            ],
        ),
    ]

    service.submit_turn(session_id, "晚上两个人吃，两菜一汤")
    result = service.submit_turn(session_id, "再加一个人")

    assert result["merged_constraints"]["diner_count"] == 3
    assert result["merged_constraints"]["evidence"]["diner_count"] == "再加一个人"
    # 每轮LLM上下文应包含本轮原文与当前约束状态
    assert "再加一个人" in llm_client.prompts[1]
    assert "diner_count" in llm_client.prompts[1]


def test_标量改_改成三个人(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_first_dinner_for_two(session_id),
        build_inherited_dinner_for_two(
            session_id,
            diner_count=3,
            evidence={"diner_count": "三个人"},
            change_actions=[
                build_top_action("diner_count", "replace", "三个人")
            ],
        ),
    ]

    service.submit_turn(session_id, "晚上两个人吃，两菜一汤")
    result = service.submit_turn(session_id, "改成三个人")

    assert result["merged_constraints"]["diner_count"] == 3


def test_标量删_人数不限(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_first_dinner_for_two(session_id),
        build_inherited_dinner_for_two(
            session_id,
            diner_count=None,
            change_actions=[
                build_top_action("diner_count", "remove", "人数不限")
            ],
        ),
    ]

    service.submit_turn(session_id, "晚上两个人吃，两菜一汤")
    result = service.submit_turn(session_id, "人数不限")

    assert result["merged_constraints"]["diner_count"] is None


def test_最长时间_改(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_turn_result(
            session_id,
            max_total_time_minutes=30,
            evidence={"max_total_time_minutes": "半小时"},
        ),
        build_turn_result(
            session_id,
            max_total_time_minutes=20,
            evidence={"max_total_time_minutes": "20分钟"},
            change_actions=[
                build_top_action(
                    "max_total_time_minutes",
                    "replace",
                    "20分钟",
                )
            ],
        ),
    ]

    service.submit_turn(session_id, "半小时内做完")
    result = service.submit_turn(session_id, "改成20分钟")

    assert result["merged_constraints"]["max_total_time_minutes"] == 20


def test_数组增_还有土豆(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_turn_result(
            session_id,
            available_ingredients=["番茄", "鸡蛋"],
            evidence={
                "available_ingredients[0]": "番茄",
                "available_ingredients[1]": "鸡蛋",
            },
        ),
        build_turn_result(
            session_id,
            available_ingredients=["番茄", "鸡蛋", "土豆"],
            evidence={"available_ingredients[2]": "土豆"},
            change_actions=[
                build_top_action(
                    "available_ingredients",
                    "add",
                    "土豆",
                )
            ],
        ),
    ]

    service.submit_turn(session_id, "家里有番茄和鸡蛋")
    result = service.submit_turn(session_id, "还有土豆")

    assert result["merged_constraints"]["available_ingredients"] == [
        "番茄",
        "鸡蛋",
        "土豆",
    ]


def test_数组删_不要土豆(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_turn_result(
            session_id,
            available_ingredients=["番茄", "鸡蛋", "土豆"],
            evidence={
                "available_ingredients[0]": "番茄",
                "available_ingredients[1]": "鸡蛋",
                "available_ingredients[2]": "土豆",
            },
        ),
        build_turn_result(
            session_id,
            available_ingredients=["番茄", "鸡蛋"],
            change_actions=[
                build_top_action(
                    "available_ingredients",
                    "remove",
                    "不要土豆",
                )
            ],
        ),
    ]

    service.submit_turn(session_id, "家里有番茄、鸡蛋和土豆")
    result = service.submit_turn(session_id, "不要土豆了")

    assert result["merged_constraints"]["available_ingredients"] == [
        "番茄",
        "鸡蛋",
    ]


def test_数组增_餐次追加保序(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_turn_result(
            session_id,
            meal_periods=["晚餐"],
            evidence={"meal_periods[0]": "今晚"},
        ),
        build_turn_result(
            session_id,
            meal_periods=["晚餐", "午餐"],
            evidence={"meal_periods[1]": "明天中午"},
            change_actions=[
                build_top_action("meal_periods", "add", "明天中午")
            ],
        ),
    ]

    service.submit_turn(session_id, "今晚吃啥")
    result = service.submit_turn(session_id, "明天中午也帮我配一下")

    assert result["merged_constraints"]["meal_periods"] == ["晚餐", "午餐"]


def test_口味增_新键(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_turn_result(
            session_id,
            meal_periods=["晚餐"],
            dishes=[build_dish(count=1, dish_type="汤")],
            evidence={
                "meal_periods[0]": "今晚",
                "dishes[0].count": "一个汤",
                "dishes[0].dish_type": "一个汤",
            },
        ),
        build_turn_result(
            session_id,
            meal_periods=["晚餐"],
            dishes=[
                build_dish(
                    count=1,
                    dish_type="汤",
                    taste_preferences={"is_light": True},
                )
            ],
            evidence={
                "dishes[0].taste_preferences.is_light": "清淡点",
            },
            change_actions=[build_dish_action(0, "replace", "清淡点")],
        ),
    ]

    service.submit_turn(session_id, "今晚做一个汤")
    result = service.submit_turn(session_id, "汤清淡点")

    assert result["merged_constraints"]["dishes"][0][
        "taste_preferences"
    ] == {"is_light": True}


def test_口味改_改口(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_turn_result(
            session_id,
            meal_periods=["晚餐"],
            dishes=[
                build_dish(
                    count=2,
                    dish_type="菜",
                    taste_preferences={"is_spicy": True},
                )
            ],
            evidence={
                "meal_periods[0]": "今晚",
                "dishes[0].count": "两个菜",
                "dishes[0].dish_type": "两个菜",
                "dishes[0].taste_preferences.is_spicy": "辣的",
            },
        ),
        build_turn_result(
            session_id,
            meal_periods=["晚餐"],
            dishes=[
                build_dish(
                    count=2,
                    dish_type="菜",
                    taste_preferences={"is_spicy": False},
                )
            ],
            evidence={"dishes[0].taste_preferences.is_spicy": "还是别辣了"},
            change_actions=[
                build_dish_action(0, "replace", "还是别辣了")
            ],
        ),
    ]

    service.submit_turn(session_id, "今晚做两个菜，要辣的")
    result = service.submit_turn(session_id, "还是别辣了")

    assert result["merged_constraints"]["dishes"][0][
        "taste_preferences"
    ] == {"is_spicy": False}


def test_口味删_放开(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_turn_result(
            session_id,
            meal_periods=["晚餐"],
            dishes=[
                build_dish(
                    count=2,
                    dish_type="菜",
                    taste_preferences={
                        "is_spicy": True,
                        "is_light": False,
                    },
                )
            ],
            evidence={
                "meal_periods[0]": "今晚",
                "dishes[0].count": "两个菜",
                "dishes[0].dish_type": "两个菜",
                "dishes[0].taste_preferences.is_spicy": "辣的",
                "dishes[0].taste_preferences.is_light": "不用清淡",
            },
        ),
        build_turn_result(
            session_id,
            meal_periods=["晚餐"],
            dishes=[
                build_dish(
                    count=2,
                    dish_type="菜",
                    taste_preferences={"is_spicy": True},
                )
            ],
            evidence={"dishes[0].taste_preferences.is_light": "不用那么清淡"},
            change_actions=[
                build_dish_action(0, "replace", "不用那么清淡")
            ],
        ),
    ]

    service.submit_turn(session_id, "今晚做两个菜，要辣的，不用清淡")
    result = service.submit_turn(session_id, "不用那么清淡了")

    assert result["merged_constraints"]["dishes"][0][
        "taste_preferences"
    ] == {"is_spicy": True}


def test_Dish增_再加一个菜(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_first_dinner_for_two(session_id),
        build_inherited_dinner_for_two(
            session_id,
            dishes=[
                build_dish(count=3, dish_type="菜"),
                build_dish(count=1, dish_type="汤"),
            ],
            evidence={"dishes[0].count": "再加一个菜"},
            change_actions=[build_dish_action(0, "add", "再加一个菜")],
        ),
    ]

    service.submit_turn(session_id, "晚上两个人吃，两菜一汤")
    result = service.submit_turn(session_id, "再加一个菜")

    assert result["merged_constraints"]["dishes"][0]["count"] == 3
    assert result["merged_constraints"]["dishes"][1]["count"] == 1


def test_Dish改_换成一道菜(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_first_dinner_for_two(session_id),
        build_inherited_dinner_for_two(
            session_id,
            dishes=[
                build_dish(count=1, dish_type="菜"),
                build_dish(count=1, dish_type="汤"),
            ],
            evidence={"dishes[0].count": "一道菜"},
            change_actions=[build_dish_action(0, "replace", "一道菜")],
        ),
    ]

    service.submit_turn(session_id, "晚上两个人吃，两菜一汤")
    result = service.submit_turn(session_id, "就换成一道菜吧")

    assert result["merged_constraints"]["dishes"][0]["count"] == 1


def test_Dish删_汤不要了(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_first_dinner_for_two(session_id),
        build_inherited_dinner_for_two(
            session_id,
            dishes=[build_dish(count=2, dish_type="菜")],
            change_actions=[build_dish_action(1, "remove", "汤不要了")],
        ),
    ]

    service.submit_turn(session_id, "晚上两个人吃，两菜一汤")
    result = service.submit_turn(session_id, "汤不要了")

    merged_dishes = result["merged_constraints"]["dishes"]
    assert len(merged_dishes) == 1
    assert merged_dishes[0]["dish_type"] == "菜"
    assert merged_dishes[0]["count"] == 2


def test_Dish增_全新菜品组(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_first_dinner_for_two(session_id),
        build_inherited_dinner_for_two(
            session_id,
            dishes=[
                build_dish(count=2, dish_type="菜"),
                build_dish(count=1, dish_type="汤"),
                build_dish(count=1, dish_type="小菜"),
            ],
            evidence={
                "dishes[2].count": "一个小菜",
                "dishes[2].dish_type": "一个小菜",
            },
            change_actions=[
                build_dish_action(None, "add", "再来一个小菜")
            ],
        ),
    ]

    service.submit_turn(session_id, "晚上两个人吃，两菜一汤")
    result = service.submit_turn(session_id, "再来一个小菜")

    merged_dishes = result["merged_constraints"]["dishes"]
    assert len(merged_dishes) == 3
    assert merged_dishes[2]["dish_type"] == "小菜"
    assert merged_dishes[2]["count"] == 1
