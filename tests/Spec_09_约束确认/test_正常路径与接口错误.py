from __future__ import annotations

import pytest

from .spec09_support import (
    FakeServiceError,
    build_get_state,
    build_merged,
    build_submit_state,
    resolved,
)


def test_正常路径_create_session返回会话编号(build_service):
    service, multi_turn, _ = build_service()

    assert service.create_session(90001) == 101
    assert multi_turn.create_calls == [90001]


def test_正常路径_submit_turn返回轮次与确认状态(build_service):
    merged = build_merged(meal_periods=["晚餐"])
    service, multi_turn, meal_period = build_service(resolved("晚餐"))
    multi_turn.submit_result = build_submit_state(merged)

    result = service.submit_turn(101, "晚饭吃什么")

    assert result["session_id"] == 101
    assert result["turn_number"] == 1
    assert result["status"] == "ready_for_planning"
    assert multi_turn.submit_calls == [(101, "晚饭吃什么")]
    assert meal_period.calls == [["晚餐"]]


def test_正常路径_get_session返回档案与确认状态(build_service):
    merged = build_merged(meal_periods=["早餐"])
    service, multi_turn, _ = build_service(resolved("早餐"))
    multi_turn.get_result = build_get_state(merged)

    result = service.get_session(101)

    assert result["session_id"] == 101
    assert result["profile_id"] == 90001
    assert result["status"] == "ready_for_planning"


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
    ("action", "error_attribute", "status_code", "message"),
    [
        ("create", "create_error", 409, "用户档案不存在"),
        ("submit", "submit_error", 400, "消息不能为空"),
        ("get", "get_error", 400, "会话不存在"),
        ("submit", "submit_error", 502, "约束冲突"),
    ],
)
def test_底层异常转换后保留状态码与信息(
    action,
    error_attribute,
    status_code,
    message,
    build_service,
    production_contract,
):
    service, multi_turn, _ = build_service()
    setattr(
        multi_turn,
        error_attribute,
        FakeServiceError(status_code, message),
    )

    with pytest.raises(production_contract.ConstraintConfirmationError) as captured:
        if action == "create":
            service.create_session(90001)
        elif action == "submit":
            service.submit_turn(101, "测试")
        else:
            service.get_session(101)

    assert captured.value.status_code == status_code
    assert str(captured.value) == message


def test_重复餐次错误保留400(build_service, production_contract):
    merged = build_merged(meal_periods=["晚餐", "晚餐"])
    service, multi_turn, _ = build_service(
        FakeServiceError(400, "餐次存在重复值")
    )
    multi_turn.get_result = build_get_state(merged)

    with pytest.raises(production_contract.ConstraintConfirmationError) as captured:
        service.get_session(101)

    assert captured.value.status_code == 400
    assert str(captured.value) == "餐次存在重复值"
