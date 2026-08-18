from __future__ import annotations

import pytest

from .spec09_support import (
    build_get_state,
    build_merged,
    build_submit_state,
    needs_confirmation,
    resolved,
)


def test_边界_初始会话返回精确空状态(build_service):
    service, multi_turn, meal_period = build_service()
    multi_turn.get_result = build_get_state(None)

    assert service.get_session(101) == {
        "session_id": 101,
        "profile_id": 90001,
        "status": "in_progress",
        "merged_constraints": None,
        "planning_context": None,
        "known_constraints": [],
        "confirmation": None,
        "message": None,
    }
    assert meal_period.calls == []


@pytest.mark.parametrize(
    ("meal_periods", "reason", "source"),
    [
        ([], "outside_meal_window", "current_time"),
        (["午餐", "晚餐"], "multiple_meal_periods", "explicit"),
        (["下午茶"], "unsupported_meal_period", "explicit"),
    ],
)
def test_边界_三类餐次不确定均返回同一句问题(
    meal_periods,
    reason,
    source,
    build_service,
):
    merged = build_merged(
        meal_periods=meal_periods,
        diner_count=2,
        total_dish_count=4,
    )
    service, multi_turn, _ = build_service(
        needs_confirmation(reason, source=source)
    )
    multi_turn.get_result = build_get_state(merged)

    result = service.get_session(101)

    assert result["status"] == "needs_confirmation"
    assert result["confirmation"]["question"] == (
        "请确认这次要安排早餐、午餐还是晚餐？"
    )
    assert [item["label"] for item in result["known_constraints"]] == [
        "人数",
        "菜品数量",
    ]


def test_边界_用户回答餐次后合并并进入可规划(build_service):
    merged = build_merged(meal_periods=["晚餐"])
    service, multi_turn, _ = build_service(resolved("晚餐"))
    multi_turn.submit_result = build_submit_state(merged, turn_number=2)

    result = service.submit_turn(101, "晚餐")

    assert result["turn_number"] == 2
    assert result["status"] == "ready_for_planning"
    assert result["merged_constraints"] is merged


def test_边界_无关回答后继续待确认(build_service):
    merged = build_merged()
    service, multi_turn, _ = build_service(
        needs_confirmation("outside_meal_window")
    )
    multi_turn.submit_result = build_submit_state(merged, turn_number=2)

    result = service.submit_turn(101, "清淡一点")

    assert result["status"] == "needs_confirmation"


def test_边界_条件齐备后不要求再次确认(build_service):
    merged = build_merged(
        meal_periods=["晚餐"],
        diner_count=2,
        total_dish_count=4,
    )
    service, multi_turn, _ = build_service(resolved("晚餐"))
    multi_turn.get_result = build_get_state(merged)

    result = service.get_session(101)

    assert result["status"] == "ready_for_planning"
    assert result["confirmation"] is None
    assert result["message"].endswith("可以开始规划。")


def test_边界_ready后新消息重新计算并展示最新约束(build_service):
    merged = build_merged(
        meal_periods=["晚餐"],
        diner_count=3,
        total_dish_count=5,
    )
    service, multi_turn, _ = build_service(resolved("晚餐"))
    multi_turn.submit_result = build_submit_state(merged, turn_number=2)

    result = service.submit_turn(101, "改成三个人五道菜")

    assert result["planning_context"]["diner_count"] == 3
    assert result["planning_context"]["total_dish_count"] == 5
    assert "- 人数：3人" in result["message"]
    assert "- 菜品数量：5道" in result["message"]


def test_边界_固定文案生成不需要注入LLM(build_service):
    merged = build_merged(meal_periods=["晚餐"])
    service, multi_turn, _ = build_service(resolved("晚餐"))
    multi_turn.get_result = build_get_state(merged)

    result = service.get_session(101)

    assert result["message"].startswith("已确定：")
    assert multi_turn.get_calls == [101]
