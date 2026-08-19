from __future__ import annotations

from sqlalchemy import select

from .spec11_support import build_turn_result


def test_create_session_正常路径(
    build_service,
    session_factory,
    profile_id,
    production_contract,
):
    service = build_service(session_factory, lambda _: {})

    session_id = service.create_session(profile_id)

    assert type(session_id) is int and session_id > 0
    state = service.get_session(session_id)
    assert state == {
        "session_id": session_id,
        "profile_id": profile_id,
        "status": "in_progress",
        "merged_constraints": None,
        "missing_requirements": ["人数", "明确菜品类型"],
    }


def test_submit_turn_单条消息作为首轮并持久化(
    start_session,
    db_session,
    production_contract,
):
    service, llm_client, session_id = start_session()
    llm_client.response = build_turn_result(
        session_id,
        meal_periods=["晚餐"],
        diner_count=2,
        evidence={"meal_periods[0]": "晚上", "diner_count": "两个人"},
    )

    result = service.submit_turn(session_id, "晚上两个人吃")

    assert result["session_id"] == session_id
    assert result["turn_number"] == 1
    assert result["status"] == "ready_for_planning"
    assert result["missing_requirements"] == ["明确菜品类型"]
    assert result["merged_constraints"] == {
        key: value
        for key, value in llm_client.response.items()
        if key != "change_actions"
    }
    row = db_session.execute(
        select(production_contract.DialogueSession).where(
            production_contract.DialogueSession.id == session_id
        )
    ).scalar_one()
    assert row.merged_constraints == result["merged_constraints"]


def test_get_session_正常路径返回最新状态(start_session):
    service, llm_client, session_id = start_session()
    llm_client.response = build_turn_result(
        session_id,
        meal_periods=["早餐"],
        evidence={"meal_periods[0]": "早餐"},
    )
    service.submit_turn(session_id, "早餐吃什么")

    state = service.get_session(session_id)

    assert state["session_id"] == session_id
    assert state["status"] == "ready_for_planning"
    assert state["merged_constraints"]["meal_periods"] == ["早餐"]
    assert state["missing_requirements"] == ["人数", "明确菜品类型"]


def test_首轮完整统一字段包含空总数和空难度(start_session):
    service, llm_client, session_id = start_session()
    llm_client.response = build_turn_result(session_id)

    result = service.submit_turn(session_id, "随便吃点")

    merged = result["merged_constraints"]
    assert set(merged) == {
        "dialogue_id",
        "meal_periods",
        "diner_count",
        "total_dish_count",
        "max_total_time_minutes",
        "max_difficulty",
        "available_ingredients",
        "dishes",
        "evidence",
    }
    assert merged["total_dish_count"] is None
    assert merged["max_difficulty"] is None
    assert merged["dishes"][0]["dish_type"] == "未指定"
