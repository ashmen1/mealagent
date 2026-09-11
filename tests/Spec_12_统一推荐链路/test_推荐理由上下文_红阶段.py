from __future__ import annotations

import copy
from typing import Any

from .conftest import (
    FakeConfirmationService,
    FakeFilteringService,
    FakeIntegrationService,
    FakePlanningService,
    build_confirmation_state,
    build_filtering_result,
    build_integrated,
    build_merged,
)


class ContextRecordingReasonService:
    """记录新理由接口的第三个参数，缺失时保留为None供断言。"""

    def __init__(self) -> None:
        self.calls: list[
            tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]
        ] = []

    def build(
        self,
        filtering_result: dict[str, Any],
        planning_result: dict[str, Any],
        decision_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                copy.deepcopy(filtering_result),
                copy.deepcopy(planning_result),
                copy.deepcopy(decision_context),
            )
        )
        return {
            "profile_id": planning_result["profile_id"],
            "dialogue_id": planning_result["dialogue_id"],
            "dish_recommendations": [],
            "filtering_reasons": [],
            "planning_reasons": [],
            "menu_reasons": [],
        }


def test_generate正常路径把生效约束和真实候选尝试传给理由服务(
    build_orchestrator,
) -> None:
    merged = build_merged(meal_periods=[])
    integrated = build_integrated(meal_periods=[])
    filtering_result = build_filtering_result(150)
    reason_service = ContextRecordingReasonService()
    service, _ = build_orchestrator(
        confirmation_service=FakeConfirmationService(
            build_confirmation_state(
                merged=merged,
                meal_period="早餐",
                meal_period_source="current_time",
            )
        ),
        integration_service=FakeIntegrationService(integrated),
        filtering_service=FakeFilteringService(filtering_result),
        planning_service=FakePlanningService(7, 9),
        reason_service=reason_service,
    )

    result = service.generate(101)

    filtering, planning, context = reason_service.calls[0]
    assert filtering == filtering_result
    assert planning == result["menu_planning_result"]
    assert context == {
        "effective_constraints": {
            **integrated,
            "meal_periods": ["早餐"],
        },
        "candidate_attempts": result["candidate_attempts"],
    }


def test_理由上下文使用副本且不回写上游结果(build_orchestrator) -> None:
    integrated = build_integrated(meal_periods=[])
    integrated_before = copy.deepcopy(integrated)
    reason_service = ContextRecordingReasonService()
    service, dependencies = build_orchestrator(
        integration_service=FakeIntegrationService(integrated),
        reason_service=reason_service,
    )

    result = service.generate(101)

    context = reason_service.calls[0][2]
    assert context is not None
    context["effective_constraints"]["meal_periods"] = ["晚餐"]
    context["candidate_attempts"].append({"伪造": True})
    assert integrated == integrated_before
    assert dependencies["integration_service"].result == integrated_before
    assert result["candidate_attempts"] != context["candidate_attempts"]


def test_理由服务收到的候选尝试包含扩展前失败和最终成功(
    build_orchestrator,
) -> None:
    reason_service = ContextRecordingReasonService()
    service, _ = build_orchestrator(
        filtering_service=FakeFilteringService(build_filtering_result(350)),
        planning_service=FakePlanningService(5, 7, 8),
        reason_service=reason_service,
    )

    result = service.generate(101)

    context = reason_service.calls[0][2]
    assert context is not None
    assert context["candidate_attempts"] == result["candidate_attempts"]
    assert [item["outcome"] for item in context["candidate_attempts"]] == [
        "below_target",
        "below_target",
        "accepted",
    ]


def test_同一会话重复调用传给理由服务的上下文完全一致(
    build_orchestrator,
) -> None:
    reason_service = ContextRecordingReasonService()
    service, _ = build_orchestrator(
        planning_service=FakePlanningService(8, 8),
        reason_service=reason_service,
    )

    first = service.generate(101)
    second = service.generate(101)

    assert first == second
    assert reason_service.calls[0][2] is not None
    assert reason_service.calls[1][2] is not None
    assert reason_service.calls[0][2] == reason_service.calls[1][2]
