from __future__ import annotations

import pytest

from .spec09_support import (
    FakeServiceError,
    build_get_result,
    build_merged,
    build_submit_result,
    needs_confirmation,
    resolved,
)


def test_创建会话委托到底层服务(build_service):
    service, multi_turn, _ = build_service()

    session_id = service.create_session(90001)

    assert session_id == 101
    assert multi_turn.created_profile_ids == [90001]


def test_初始会话返回精确空状态(build_service):
    service, multi_turn, meal_period = build_service()
    multi_turn.get_result = build_get_result(None)

    result = service.get_session(101)

    assert result == {
        "session_id": 101,
        "profile_id": 90001,
        "status": "in_progress",
        "merged_constraints": None,
        "planning_context": None,
        "known_constraints": [],
        "confirmation": None,
        "message": None,
    }
    assert meal_period.inputs == []


def test_提交消息只委托一次并保留轮次信息(build_service):
    merged = build_merged(meal_periods=["晚餐"])
    service, multi_turn, meal_period = build_service(resolved("晚餐"))
    multi_turn.submit_result = build_submit_result(merged)

    result = service.submit_turn(101, "晚饭吃什么")

    assert result["session_id"] == 101
    assert result["turn_number"] == 1
    assert result["status"] == "ready_for_planning"
    assert multi_turn.submitted_turns == [(101, "晚饭吃什么")]
    assert meal_period.inputs == [["晚餐"]]


@pytest.mark.parametrize("method_name", ["create_session", "submit_turn", "get_session"])
def test_底层错误转换后保留状态码与信息(
    method_name,
    build_service,
    production_contract,
):
    service, multi_turn, _ = build_service()
    error = FakeServiceError(409, "底层业务错误")
    setattr(multi_turn, f"{method_name.split('_')[0]}_error", error)

    with pytest.raises(production_contract.ConstraintConfirmationError) as captured:
        if method_name == "create_session":
            service.create_session(90001)
        elif method_name == "submit_turn":
            service.submit_turn(101, "测试")
        else:
            service.get_session(101)

    assert captured.value.status_code == 409
    assert str(captured.value) == "底层业务错误"


def test_餐次解析错误转换后保留状态码与信息(
    build_service,
    production_contract,
):
    merged = build_merged()
    service, multi_turn, _ = build_service(
        FakeServiceError(500, "时钟读取失败")
    )
    multi_turn.get_result = build_get_result(merged)

    with pytest.raises(production_contract.ConstraintConfirmationError) as captured:
        service.get_session(101)

    assert captured.value.status_code == 500
    assert str(captured.value) == "时钟读取失败"


@pytest.mark.parametrize(
    ("multi_turn", "meal_period"),
    [(None, object()), (object(), None), (object(), object())],
)
def test_依赖无效返回500(
    multi_turn,
    meal_period,
    production_contract,
):
    with pytest.raises(production_contract.ConstraintConfirmationError) as captured:
        production_contract.ConstraintConfirmationService(
            multi_turn,
            meal_period,
        )

    assert captured.value.status_code == 500


@pytest.mark.parametrize(
    "reason",
    [
        "outside_meal_window",
        "multiple_meal_periods",
        "unsupported_meal_period",
    ],
)
def test_餐次不确定统一返回固定问题(reason, build_service):
    merged = build_merged(diner_count=2, total_dish_count=4)
    service, multi_turn, _ = build_service(needs_confirmation(reason))
    multi_turn.get_result = build_get_result(merged)

    result = service.get_session(101)

    assert result["status"] == "needs_confirmation"
    assert result["confirmation"] == {
        "reason": reason,
        "options": ["早餐", "午餐", "晚餐"],
        "question": "请确认这次要安排早餐、午餐还是晚餐？",
    }
    assert result["message"].endswith(
        "还需要确认：\n请确认这次要安排早餐、午餐还是晚餐？"
    )


def test_无关回答后仍可继续待确认(build_service):
    merged = build_merged()
    service, multi_turn, _ = build_service(
        needs_confirmation("outside_meal_window")
    )
    multi_turn.submit_result = build_submit_result(merged)

    result = service.submit_turn(101, "清淡一点")

    assert result["status"] == "needs_confirmation"
    assert result["merged_constraints"] is merged
