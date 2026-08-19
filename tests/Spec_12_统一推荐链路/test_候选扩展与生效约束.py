from __future__ import annotations

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
    build_merged,
)


def test_候选按100_300_全量扩展并在达到8分时结束(
    build_orchestrator,
) -> None:
    filtering = FakeFilteringService(build_filtering_result(350, 120))
    planning = FakePlanningService(5, 7, 8)
    service, _ = build_orchestrator(
        filtering_service=filtering,
        planning_service=planning,
        integration_service=FakeIntegrationService(
            build_integrated(
                dishes=[build_integrated_dish(), build_integrated_dish()]
            )
        ),
    )

    result = service.generate(101)

    assert result["status"] == "recommended"
    assert [item["candidate_limit"] for item in result["candidate_attempts"]] == [
        100,
        300,
        None,
    ]
    assert [item["candidate_counts"] for item in result["candidate_attempts"]] == [
        [100, 100],
        [300, 120],
        [350, 120],
    ]
    assert [item["outcome"] for item in result["candidate_attempts"]] == [
        "below_target",
        "below_target",
        "accepted",
    ]
    assert [item["nutrition_score"] for item in result["candidate_attempts"]] == [
        5,
        7,
        8,
    ]
    assert len(planning.calls[0]["dishes"][0]["candidates"]) == 100
    assert len(planning.calls[1]["dishes"][0]["candidates"]) == 300
    assert len(planning.calls[2]["dishes"][0]["candidates"]) == 350


def test_首次候选已是全量时只规划一次且limit为null(
    build_orchestrator,
) -> None:
    planning = FakePlanningService(8)
    service, _ = build_orchestrator(
        filtering_service=FakeFilteringService(build_filtering_result(50, 2)),
        planning_service=planning,
        integration_service=FakeIntegrationService(
            build_integrated(
                dishes=[build_integrated_dish(), build_integrated_dish()]
            )
        ),
    )

    result = service.generate(101)

    assert result["candidate_attempts"] == [
        {
            "candidate_limit": None,
            "candidate_counts": [50, 2],
            "outcome": "accepted",
            "nutrition_score": 8,
        }
    ]
    assert len(planning.calls) == 1


def test_不可行后扩展并在下一阶段成功(build_orchestrator) -> None:
    planning = FakePlanningService(FakeDependencyError(422, "不可行"), 9)
    service, _ = build_orchestrator(
        filtering_service=FakeFilteringService(build_filtering_result(150)),
        planning_service=planning,
    )

    result = service.generate(101)

    assert result["status"] == "recommended"
    assert result["candidate_attempts"] == [
        {
            "candidate_limit": 100,
            "candidate_counts": [100],
            "outcome": "infeasible",
            "nutrition_score": None,
        },
        {
            "candidate_limit": None,
            "candidate_counts": [150],
            "outcome": "accepted",
            "nutrition_score": 9,
        },
    ]


def test_全量得分仍低于8时返回菜单和唯一质量警告(
    build_orchestrator,
) -> None:
    service, _ = build_orchestrator(
        filtering_service=FakeFilteringService(build_filtering_result(150)),
        planning_service=FakePlanningService(5, 7),
    )

    result = service.generate(101)

    assert result["status"] == "recommended"
    assert result["menu_planning_result"]["nutrition_score"] == 7
    assert result["quality_warnings"] == [
        {
            "code": "nutrition_score_below_target",
            "nutrition_score": 7,
            "target_score": 8,
        }
    ]


def test_0分是可返回菜单的合法低分极值(build_orchestrator) -> None:
    service, _ = build_orchestrator(
        planning_service=FakePlanningService(0)
    )

    result = service.generate(101)

    assert result["status"] == "recommended"
    assert result["quality_warnings"][0]["nutrition_score"] == 0


def test_16分直接接受且不产生警告(build_orchestrator) -> None:
    service, _ = build_orchestrator(
        planning_service=FakePlanningService(16)
    )

    result = service.generate(101)

    assert result["candidate_attempts"][0]["outcome"] == "accepted"
    assert result["quality_warnings"] == []


def test_系统推定餐次写入筛选副本但不回写会话(
    build_orchestrator,
) -> None:
    merged = build_merged(meal_periods=[])
    confirmation = FakeConfirmationService(
        build_confirmation_state(
            merged=merged,
            meal_period="午餐",
            meal_period_source="current_time",
        )
    )
    integration = FakeIntegrationService(build_integrated(meal_periods=[]))
    filtering = FakeFilteringService(build_filtering_result(1))
    service, _ = build_orchestrator(
        confirmation_service=confirmation,
        integration_service=integration,
        filtering_service=filtering,
    )

    service.generate(101)

    assert filtering.calls[0]["meal_periods"] == ["午餐"]
    assert merged["meal_periods"] == []
    assert integration.result["meal_periods"] == []


def test_全量候选营养只按首次出现顺序加载一次(
    build_orchestrator,
) -> None:
    filtering_result = build_filtering_result(3, 2)
    filtering_result["dishes"][1][0] = filtering_result["dishes"][0][1]
    service, dependencies = build_orchestrator(
        filtering_service=FakeFilteringService(filtering_result),
        integration_service=FakeIntegrationService(
            build_integrated(
                dishes=[build_integrated_dish(), build_integrated_dish()]
            )
        ),
    )

    service.generate(101)

    assert dependencies["nutrition_service"].recipe_calls == [
        ["第0组菜000", "第0组菜001", "第0组菜002", "第1组菜001"]
    ]
    assert dependencies["nutrition_service"].target_calls == [(25, "午餐")]


def test_推荐理由使用全量筛选结果和最终规划结果(
    build_orchestrator,
) -> None:
    filtering_result = build_filtering_result(150)
    service, dependencies = build_orchestrator(
        filtering_service=FakeFilteringService(filtering_result),
        planning_service=FakePlanningService(5, 8),
    )

    result = service.generate(101)

    reason_call = dependencies["reason_service"].calls[0]
    assert reason_call[0] == filtering_result
    assert reason_call[1] == result["menu_planning_result"]


def test_相同输入和依赖结果重复调用结构完全一致(
    build_orchestrator,
) -> None:
    service, _ = build_orchestrator(
        planning_service=FakePlanningService(8, 8)
    )

    first = service.generate(101)
    second = service.generate(101)

    assert first == second
