from __future__ import annotations

from .spec08_support import (
    FakeLLMClient,
    build_dish,
    build_dish_action,
    build_first_dinner_for_two,
    build_inherited_dinner_for_two,
    build_top_action,
    build_turn_result,
)


def test_今晚三个人吃_ready(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_turn_result(
            session_id,
            meal_periods=["晚餐"],
            diner_count=3,
            evidence={
                "meal_periods[0]": "今晚",
                "diner_count": "三个人",
            },
        )
    ]

    result = service.submit_turn(session_id, "今晚三个人吃")

    assert result["status"] == "ready_for_planning"
    assert result["missing_requirements"] == ["明确菜品类型"]


def test_多餐次_needs_confirmation_澄清后ready(start_session):
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
        build_turn_result(
            session_id,
            meal_periods=["晚餐"],
            change_actions=[
                build_top_action("meal_periods", "remove", "午餐不要了")
            ],
        ),
    ]

    assert service.submit_turn(session_id, "今晚吃啥")["status"] == (
        "ready_for_planning"
    )
    assert service.submit_turn(session_id, "明天中午也配一下")["status"] == (
        "needs_confirmation"
    )
    assert service.submit_turn(session_id, "午餐不要了")["status"] == (
        "ready_for_planning"
    )


def test_时间窗外_needs_confirmation(build_service, session_factory, profile_id, clock_at):
    llm_client = FakeLLMClient()
    service = build_service(session_factory, llm_client, clock=clock_at(3, 0))
    session_id = service.create_session(profile_id)
    llm_client.responses = [
        build_turn_result(session_id),
        build_turn_result(
            session_id,
            meal_periods=["晚餐"],
            evidence={"meal_periods[0]": "晚饭"},
            change_actions=[
                build_top_action("meal_periods", "add", "晚饭")
            ],
        ),
    ]

    assert service.submit_turn(session_id, "吃啥好呢")["status"] == (
        "needs_confirmation"
    )
    assert service.submit_turn(session_id, "晚饭吧")["status"] == (
        "ready_for_planning"
    )


def test_下午茶_needs_confirmation(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_turn_result(
            session_id,
            meal_periods=["下午茶"],
            evidence={"meal_periods[0]": "下午茶"},
        )
    ]

    result = service.submit_turn(session_id, "下午茶吃点啥")

    assert result["status"] == "needs_confirmation"


def test_今晚吃啥_missing_人数与明确菜品类型(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_turn_result(
            session_id,
            meal_periods=["晚餐"],
            evidence={"meal_periods[0]": "今晚"},
        )
    ]

    result = service.submit_turn(session_id, "今晚吃啥")

    assert result["status"] == "ready_for_planning"
    assert result["missing_requirements"] == ["人数", "明确菜品类型"]


def test_missing_requirements为空(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [build_first_dinner_for_two(session_id)]

    result = service.submit_turn(session_id, "晚上两个人吃，两菜一汤")

    assert result["status"] == "ready_for_planning"
    assert result["missing_requirements"] == []


def test_未指定菜仅有数量_仍缺明确菜品类型(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_turn_result(
            session_id,
            meal_periods=["晚餐"],
            diner_count=2,
            dishes=[build_dish(count=3)],
            evidence={
                "meal_periods[0]": "今晚",
                "diner_count": "两个人",
                "dishes[0].count": "三个",
            },
        )
    ]

    result = service.submit_turn(session_id, "今晚两个人吃，来三个")

    assert result["status"] == "ready_for_planning"
    assert result["missing_requirements"] == ["明确菜品类型"]


def test_未指定菜有必需食材_仍缺明确菜品类型(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_turn_result(
            session_id,
            meal_periods=["晚餐"],
            dishes=[
                build_dish(
                    required_ingredients=[
                        {"kind": "concept", "value": "面"}
                    ]
                )
            ],
            evidence={
                "meal_periods[0]": "今晚",
                "dishes[0].required_ingredients[0].value": "面",
            },
        )
    ]

    result = service.submit_turn(session_id, "今晚想吃面")

    assert result["status"] == "ready_for_planning"
    assert result["missing_requirements"] == ["人数", "明确菜品类型"]


def test_ready后新轮_重新判定仍ready(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_first_dinner_for_two(session_id),
        build_inherited_dinner_for_two(
            session_id,
            dishes=[
                {
                    **build_first_dinner_for_two(session_id)["dishes"][0],
                    "count": 3,
                },
                build_first_dinner_for_two(session_id)["dishes"][1],
            ],
            evidence={"dishes[0].count": "再加一个菜"},
            change_actions=[build_dish_action(0, "add", "再加一个菜")],
        ),
    ]

    assert service.submit_turn(
        session_id,
        "晚上两个人吃，两菜一汤",
    )["status"] == "ready_for_planning"

    result = service.submit_turn(session_id, "再加一个菜")

    assert result["turn_number"] == 2
    assert result["status"] == "ready_for_planning"
    assert result["merged_constraints"]["dishes"][0]["count"] == 3


def test_ready后新轮_多餐次_needs_confirmation(start_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_first_dinner_for_two(session_id),
        build_inherited_dinner_for_two(
            session_id,
            meal_periods=["晚餐", "午餐"],
            evidence={"meal_periods[1]": "明天中午"},
            change_actions=[
                build_top_action("meal_periods", "add", "明天中午")
            ],
        ),
    ]

    assert service.submit_turn(
        session_id,
        "晚上两个人吃，两菜一汤",
    )["status"] == "ready_for_planning"

    result = service.submit_turn(session_id, "明天中午也配一下")

    assert result["turn_number"] == 2
    assert result["status"] == "needs_confirmation"
