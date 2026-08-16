from __future__ import annotations

import pytest
from sqlalchemy import select

from .spec08_support import FakeLLMClient, build_turn_result


def test_create_session_正常(production_contract, build_service, session_factory, profile_id, db_session):
    service = build_service(session_factory, FakeLLMClient())
    session_id = service.create_session(profile_id)

    assert type(session_id) is int and session_id > 0
    row = db_session.execute(
        select(production_contract.DialogueSession).where(
            production_contract.DialogueSession.id == session_id
        )
    ).scalar_one()
    assert row.profile_id == profile_id
    assert row.status == "in_progress"
    assert row.merged_constraints is None


@pytest.mark.parametrize("invalid_profile_id", [0, -1, "1", None, 1.5])
def test_create_session_非法profile_id_400(
    invalid_profile_id,
    build_service,
    session_factory,
    assert_multi_turn_error,
):
    service = build_service(session_factory, FakeLLMClient())
    assert_multi_turn_error(
        lambda: service.create_session(invalid_profile_id),
        400,
    )


def test_create_session_profile不存在_409(
    build_service,
    session_factory,
    assert_multi_turn_error,
):
    service = build_service(session_factory, FakeLLMClient())
    assert_multi_turn_error(lambda: service.create_session(999999), 409)


def test_get_session_正常(build_service, session_factory, profile_id):
    llm_client = FakeLLMClient()
    service = build_service(session_factory, llm_client)
    session_id = service.create_session(profile_id)
    llm_client.responses = [
        build_turn_result(
            session_id,
            meal_periods=["晚餐"],
            evidence={"meal_periods[0]": "今晚"},
        )
    ]
    service.submit_turn(session_id, "今晚吃啥")

    state = service.get_session(session_id)

    assert state["session_id"] == session_id
    assert state["profile_id"] == profile_id
    assert state["status"] == "ready_for_planning"
    assert state["merged_constraints"]["meal_periods"] == ["晚餐"]
    assert state["missing_requirements"] == ["人数", "明确菜品类型"]


def test_get_session_会话不存在_400(
    build_service,
    session_factory,
    assert_multi_turn_error,
):
    service = build_service(session_factory, FakeLLMClient())
    assert_multi_turn_error(lambda: service.get_session(999999), 400)
