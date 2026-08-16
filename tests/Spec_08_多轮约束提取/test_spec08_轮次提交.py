from __future__ import annotations

import pytest
from sqlalchemy import select

from .spec08_support import FakeLLMClient, build_turn_result


def test_submit_turn_首轮正常_返回与落库(
    production_contract,
    start_session,
    db_session,
):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_turn_result(
            session_id,
            meal_periods=["晚餐"],
            diner_count=2,
            evidence={
                "meal_periods[0]": "晚上",
                "diner_count": "两个人",
            },
        )
    ]

    result = service.submit_turn(session_id, "晚上两个人吃")

    assert result["session_id"] == session_id
    assert result["turn_number"] == 1
    assert result["status"] == "ready_for_planning"
    assert result["merged_constraints"]["meal_periods"] == ["晚餐"]
    assert result["merged_constraints"]["diner_count"] == 2
    assert "change_actions" not in result["merged_constraints"]
    assert result["missing_requirements"] == ["明确菜品类型"]

    turns = db_session.execute(
        select(production_contract.DialogueTurn).where(
            production_contract.DialogueTurn.session_id == session_id
        )
    ).scalars().all()
    assert len(turns) == 1
    assert turns[0].turn_number == 1
    assert turns[0].user_message == "晚上两个人吃"

    session_row = db_session.execute(
        select(production_contract.DialogueSession).where(
            production_contract.DialogueSession.id == session_id
        )
    ).scalar_one()
    assert session_row.status == "ready_for_planning"
    assert session_row.merged_constraints == result["merged_constraints"]


def test_submit_turn_会话不存在_400_不调用LLM(
    start_session,
    assert_multi_turn_error,
):
    service, llm_client, session_id = start_session()
    assert_multi_turn_error(
        lambda: service.submit_turn(session_id + 999, "今晚吃啥"),
        400,
    )
    assert llm_client.call_count == 0


@pytest.mark.parametrize("invalid_message", ["", "   ", 123, None, ["今晚吃啥"]])
def test_submit_turn_消息非法_400_不调用LLM(
    invalid_message,
    start_session,
    assert_multi_turn_error,
):
    service, llm_client, session_id = start_session()
    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, invalid_message),
        400,
    )
    assert llm_client.call_count == 0


def test_turn_number严格递增(production_contract, start_session, db_session):
    service, llm_client, session_id = start_session()
    llm_client.responses = [
        build_turn_result(
            session_id,
            meal_periods=["晚餐"],
            evidence={"meal_periods[0]": "今晚"},
        ),
        build_turn_result(
            session_id,
            meal_periods=["晚餐"],
            evidence={"meal_periods[0]": "今晚"},
        ),
        build_turn_result(
            session_id,
            meal_periods=["晚餐"],
            evidence={"meal_periods[0]": "今晚"},
        ),
    ]

    service.submit_turn(session_id, "今晚吃啥")
    service.submit_turn(session_id, "今晚吃啥")
    service.submit_turn(session_id, "今晚吃啥")

    turns = db_session.execute(
        select(production_contract.DialogueTurn).where(
            production_contract.DialogueTurn.session_id == session_id
        )
    ).scalars().all()
    assert [turn.turn_number for turn in turns] == [1, 2, 3]


def test_轮次提取失败不落库_会话状态不变(
    production_contract,
    start_session,
    db_session,
    assert_multi_turn_error,
):
    service, llm_client, session_id = start_session()
    # 首轮 diner_count=2 但缺少 evidence,校验失败;重试一次仍失败
    llm_client.responses = [
        build_turn_result(session_id, diner_count=2),
        build_turn_result(session_id, diner_count=2),
    ]

    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "两个人"),
        502,
    )
    assert llm_client.call_count == 2

    turns = db_session.execute(
        select(production_contract.DialogueTurn).where(
            production_contract.DialogueTurn.session_id == session_id
        )
    ).scalars().all()
    assert turns == []

    session_row = db_session.execute(
        select(production_contract.DialogueSession).where(
            production_contract.DialogueSession.id == session_id
        )
    ).scalar_one()
    assert session_row.status == "in_progress"
    assert session_row.merged_constraints is None
