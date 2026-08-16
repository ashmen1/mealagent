from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .spec08_support import FakeLLMClient, build_turn_result


def test_session状态非法_数据库约束(
    production_contract,
    db_session,
    profile_id,
):
    session_row = production_contract.DialogueSession(
        profile_id=profile_id,
        status="bogus_status",
        merged_constraints=None,
    )
    db_session.add(session_row)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_session_profile外键约束(production_contract, db_session):
    session_row = production_contract.DialogueSession(
        profile_id=999999,
        status="in_progress",
        merged_constraints=None,
    )
    db_session.add(session_row)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_turn联合唯一约束(production_contract, db_session, profile_id):
    session_row = production_contract.DialogueSession(
        profile_id=profile_id,
        status="in_progress",
        merged_constraints=None,
    )
    db_session.add(session_row)
    db_session.commit()
    db_session.add(
        production_contract.DialogueTurn(
            session_id=session_row.id,
            turn_number=1,
            user_message="今晚吃啥",
        )
    )
    db_session.commit()
    db_session.add(
        production_contract.DialogueTurn(
            session_id=session_row.id,
            turn_number=1,
            user_message="再来一个",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_turn_session外键约束(production_contract, db_session):
    db_session.add(
        production_contract.DialogueTurn(
            session_id=999999,
            turn_number=1,
            user_message="今晚吃啥",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_成功落库_表结构符合Spec(
    production_contract,
    db_session,
    build_service,
    session_factory,
    profile_id,
):
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

    session_row = db_session.execute(
        select(production_contract.DialogueSession).where(
            production_contract.DialogueSession.id == session_id
        )
    ).scalar_one()
    assert session_row.profile_id == profile_id
    assert session_row.status == "ready_for_planning"
    assert session_row.merged_constraints["meal_periods"] == ["晚餐"]

    turn_row = db_session.execute(
        select(production_contract.DialogueTurn).where(
            production_contract.DialogueTurn.session_id == session_id
        )
    ).scalar_one()
    assert turn_row.turn_number == 1
    assert turn_row.user_message == "今晚吃啥"
