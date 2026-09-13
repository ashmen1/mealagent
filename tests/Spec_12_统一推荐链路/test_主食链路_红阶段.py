from __future__ import annotations

from .conftest import (
    FakeConfirmationService,
    FakeFilteringService,
    FakeIntegrationService,
    FakePlanningService,
    FakeReasonService,
    build_candidate,
    build_confirmation_state,
    build_filtering_result,
    build_integrated,
    build_integrated_dish,
    build_merged,
)


def _staple_dish():
    return build_integrated_dish(
        dish_type="主食",
        required_staple_ingredients={
            "match": "any",
            "items": ["玉米", "红薯"],
        },
        excluded_staple_ingredients=["米饭"],
    )


def test_generate正常路径将主食字段贯穿筛选和推荐理由(
    build_orchestrator,
) -> None:
    merged = build_merged(
        meal_periods=["晚餐"],
        dishes=[_staple_dish()],
    )
    integrated = build_integrated(
        meal_periods=["晚餐"],
        dishes=[_staple_dish()],
    )
    filtering = FakeFilteringService(build_filtering_result(1))
    reasons = FakeReasonService()
    service, _ = build_orchestrator(
        confirmation_service=FakeConfirmationService(
            build_confirmation_state(merged=merged, meal_period="晚餐")
        ),
        integration_service=FakeIntegrationService(integrated),
        filtering_service=filtering,
        reason_service=reasons,
    )

    result = service.generate(101)

    assert result["status"] == "recommended"
    assert filtering.calls[0]["dishes"][0] == _staple_dish()
    context = reasons.calls[0][2]
    assert context["effective_constraints"]["dishes"][0] == _staple_dish()


def test_主食约束不改变候选扩展与规划规则(build_orchestrator) -> None:
    candidates = {
        "dishes": [
            [
                build_candidate(
                    "蜜汁烤玉米" if index == 0 else f"主食候选{index:03d}",
                    recipe_type="主食",
                )
                for index in range(150)
            ]
        ],
        "unmatched_allergens": [],
    }
    service, dependencies = build_orchestrator(
        integration_service=FakeIntegrationService(
            build_integrated(dishes=[_staple_dish()])
        ),
        filtering_service=FakeFilteringService(candidates),
        planning_service=FakePlanningService(7, 8),
    )

    result = service.generate(101)

    assert result["status"] == "recommended"
    assert [item["candidate_limit"] for item in result["candidate_attempts"]] == [
        100,
        None,
    ]
    assert len(dependencies["planning_service"].calls) == 2


def test_主食来源与档案过敏冲突时不进入筛选和规划(
    build_orchestrator,
) -> None:
    conflict = {
        "code": "allergen_required_ingredient",
        "dish_index": 0,
        "profile_path": "allergens[0]",
        "dialogue_path": "dishes[0].required_staple_ingredients.items[0]",
        "allergen": "玉米",
        "required_ingredient": {"kind": "ingredient", "value": "玉米"},
        "dialogue_evidence": "玉米作主食",
    }
    integrated = build_integrated(
        allergens=["玉米"],
        dishes=[
            build_integrated_dish(
                dish_type="主食",
                required_staple_ingredients={
                    "match": "all",
                    "items": ["玉米"],
                },
            )
        ],
        has_conflicts=True,
        conflicts=[conflict],
    )
    filtering = FakeFilteringService()
    planning = FakePlanningService()
    service, _ = build_orchestrator(
        integration_service=FakeIntegrationService(integrated),
        filtering_service=filtering,
        planning_service=planning,
    )

    result = service.generate(101)

    assert result["status"] == "constraint_conflict"
    assert result["conflicts"] == [conflict]
    assert filtering.calls == []
    assert planning.calls == []
