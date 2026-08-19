from __future__ import annotations

import copy

import pytest

from .conftest import (
    FakeConfirmationService,
    FakeDependencyError,
    FakeFilteringService,
    FakeIntegrationService,
    FakePlanningService,
    build_confirmation_state,
    build_filtering_result,
    build_integrated,
    build_integrated_dish,
)


RESULT_FIELDS = {
    "session_id",
    "profile_id",
    "dialogue_id",
    "status",
    "confirmation_state",
    "conflicts",
    "unmatched_allergens",
    "empty_dish_indexes",
    "dish_filtering_result",
    "candidate_attempts",
    "menu_planning_result",
    "recommendation_reason_result",
    "quality_warnings",
}


def test_统一入口正常推荐返回固定完整结构(build_orchestrator) -> None:
    service, _ = build_orchestrator()

    result = service.generate(101)

    assert set(result) == RESULT_FIELDS
    assert result["status"] == "recommended"
    assert result["session_id"] == 101
    assert result["profile_id"] == 25
    assert result["dialogue_id"] == 101
    assert result["menu_planning_result"] is not None
    assert result["recommendation_reason_result"] is not None


def test_尚无成功轮次返回in_progress且不调用下游(build_orchestrator) -> None:
    confirmation = FakeConfirmationService(
        build_confirmation_state(status="in_progress")
    )
    service, dependencies = build_orchestrator(
        confirmation_service=confirmation
    )

    result = service.generate(101)

    assert result["status"] == "in_progress"
    assert result["dialogue_id"] == 101
    assert dependencies["profile_service"].calls == []
    assert result["dish_filtering_result"] is None


def test_餐次待确认返回needs_confirmation且不调用下游(
    build_orchestrator,
) -> None:
    confirmation = FakeConfirmationService(
        build_confirmation_state(
            status="needs_confirmation",
            meal_period=None,
            meal_period_source=None,
        )
    )
    service, dependencies = build_orchestrator(
        confirmation_service=confirmation
    )

    result = service.generate(101)

    assert result["status"] == "needs_confirmation"
    assert result["confirmation_state"]["confirmation"]["question"] == (
        "请确认这次要安排早餐、午餐还是晚餐？"
    )
    assert dependencies["profile_service"].calls == []


def test_整合冲突返回constraint_conflict(build_orchestrator) -> None:
    conflict = {
        "code": "allergen_required_ingredient",
        "dish_index": 0,
        "profile_path": "allergens[0]",
        "dialogue_path": (
            "dishes[0].required_ingredient_groups[0].items[0].value"
        ),
        "allergen": "花生",
        "required_ingredient": {"kind": "ingredient", "value": "花生"},
        "dialogue_evidence": "花生",
    }
    integration = FakeIntegrationService(
        build_integrated(has_conflicts=True, conflicts=[conflict])
    )
    service, dependencies = build_orchestrator(
        integration_service=integration
    )

    result = service.generate(101)

    assert result["status"] == "constraint_conflict"
    assert result["conflicts"] == [conflict]
    assert dependencies["filtering_service"].calls == []


def test_未解析过敏词返回unmatched_allergen(build_orchestrator) -> None:
    filtering = FakeFilteringService(build_filtering_result(1))
    filtering.result["unmatched_allergens"] = ["贝壳类"]
    service, dependencies = build_orchestrator(
        filtering_service=filtering
    )

    result = service.generate(101)

    assert result["status"] == "unmatched_allergen"
    assert result["unmatched_allergens"] == ["贝壳类"]
    assert dependencies["nutrition_service"].recipe_calls == []


def test_全量候选存在空组返回empty_candidate(build_orchestrator) -> None:
    filtering = FakeFilteringService(build_filtering_result(2, 0, 1))
    service, dependencies = build_orchestrator(
        filtering_service=filtering,
        integration_service=FakeIntegrationService(
            build_integrated(
                dishes=[
                    build_integrated_dish(),
                    build_integrated_dish(),
                    build_integrated_dish(),
                ]
            )
        ),
    )

    result = service.generate(101)

    assert result["status"] == "empty_candidate"
    assert result["empty_dish_indexes"] == [1]
    assert dependencies["nutrition_service"].recipe_calls == []


def test_全量规划仍不可行返回planning_infeasible(
    build_orchestrator,
) -> None:
    filtering = FakeFilteringService(build_filtering_result(350))
    planning = FakePlanningService(
        FakeDependencyError(422, "不可行"),
        FakeDependencyError(422, "不可行"),
        FakeDependencyError(422, "不可行"),
    )
    service, _ = build_orchestrator(
        filtering_service=filtering,
        planning_service=planning,
    )

    result = service.generate(101)

    assert result["status"] == "planning_infeasible"
    assert result["candidate_attempts"] == [
        {
            "candidate_limit": 100,
            "candidate_counts": [100],
            "outcome": "infeasible",
            "nutrition_score": None,
        },
        {
            "candidate_limit": 300,
            "candidate_counts": [300],
            "outcome": "infeasible",
            "nutrition_score": None,
        },
        {
            "candidate_limit": None,
            "candidate_counts": [350],
            "outcome": "infeasible",
            "nutrition_score": None,
        },
    ]
    assert result["menu_planning_result"] is None


def test_非业务依赖异常统一抛500(build_orchestrator) -> None:
    planning = FakePlanningService(FakeDependencyError(500, "数据库故障"))
    service, _ = build_orchestrator(planning_service=planning)

    with pytest.raises(Exception) as captured:
        service.generate(101)

    assert getattr(captured.value, "status_code", None) == 500
    assert "数据库故障" in str(captured.value)


def test_筛选候选组数与整合Dish不一致返回500(build_orchestrator) -> None:
    service, _ = build_orchestrator(
        filtering_service=FakeFilteringService(build_filtering_result(1, 1))
    )

    with pytest.raises(Exception) as captured:
        service.generate(101)

    assert getattr(captured.value, "status_code", None) == 500
    assert "候选组" in str(captured.value)


def test_筛选候选组不是数组时返回500而不是空候选(
    build_orchestrator,
) -> None:
    filtering = FakeFilteringService(build_filtering_result(1))
    filtering.result["dishes"] = [None]
    service, _ = build_orchestrator(filtering_service=filtering)

    with pytest.raises(Exception) as captured:
        service.generate(101)

    assert getattr(captured.value, "status_code", None) == 500
    assert "候选组" in str(captured.value)


@pytest.mark.parametrize("bad_session_id", [None, True, 0, -1, "101"])
def test_session_id非法返回400(build_orchestrator, bad_session_id) -> None:
    service, dependencies = build_orchestrator()

    with pytest.raises(Exception) as captured:
        service.generate(bad_session_id)

    assert getattr(captured.value, "status_code", None) == 400
    assert dependencies["confirmation_service"].calls == []


def test_generate不修改确认状态和依赖结果(build_orchestrator) -> None:
    confirmation_state = build_confirmation_state()
    filtering_result = build_filtering_result(2)
    confirmation_snapshot = copy.deepcopy(confirmation_state)
    filtering_snapshot = copy.deepcopy(filtering_result)
    service, _ = build_orchestrator(
        confirmation_service=FakeConfirmationService(confirmation_state),
        filtering_service=FakeFilteringService(filtering_result),
    )

    service.generate(101)

    assert confirmation_state == confirmation_snapshot
    assert filtering_result == filtering_snapshot
