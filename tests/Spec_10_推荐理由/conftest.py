from __future__ import annotations

import importlib
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


TEST_DIR = Path(__file__).resolve().parent
REPO_ROOT = TEST_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def production_contract():
    try:
        module = importlib.import_module(
            "backend.services.recommendation_reason"
        )
        return SimpleNamespace(
            RecommendationReasonService=module.RecommendationReasonService,
            RecommendationReasonError=module.RecommendationReasonError,
        )
    except (ModuleNotFoundError, AttributeError) as exc:
        pytest.fail(
            "红阶段缺少预期生产接口："
            "backend.services.recommendation_reason."
            "RecommendationReasonService / RecommendationReasonError；"
            f"原始错误：{exc}",
            pytrace=False,
        )


@pytest.fixture
def invoke_build(production_contract) -> Callable[..., dict[str, Any]]:
    def invoke(
        dish_filtering_result: object,
        menu_planning_result: object,
        decision_context: object | None = None,
    ) -> dict[str, Any]:
        service = production_contract.RecommendationReasonService()
        context = (
            _build_decision_context(
                dish_filtering_result,
                menu_planning_result,
            )
            if decision_context is None
            else decision_context
        )
        return service.build(
            dish_filtering_result,
            menu_planning_result,
            context,
        )

    return invoke


@pytest.fixture
def assert_reason_error(invoke_build):
    def assert_error(
        dish_filtering_result: object,
        menu_planning_result: object,
        expected_status: int,
    ) -> Exception:
        with pytest.raises(Exception) as captured:
            invoke_build(dish_filtering_result, menu_planning_result)
        assert getattr(captured.value, "status_code", None) == expected_status
        return captured.value

    return assert_error


def _build_decision_context(
    dish_filtering_result: object,
    menu_planning_result: object,
) -> dict[str, Any]:
    filtering = (
        dish_filtering_result
        if isinstance(dish_filtering_result, dict)
        else {}
    )
    planning = (
        menu_planning_result
        if isinstance(menu_planning_result, dict)
        else {}
    )
    raw_groups = filtering.get("dishes")
    groups = raw_groups if isinstance(raw_groups, list) else [[]]
    counts = [len(group) if isinstance(group, list) else 0 for group in groups]
    limit = None if all(count <= 100 for count in counts) else 100
    staged_counts = [min(100, count) for count in counts]
    score = planning.get("nutrition_score", 9)
    diner_count = planning.get("diner_count", 2)
    return {
        "effective_constraints": {
            "profile_id": planning.get("profile_id", 25),
            "dialogue_id": planning.get("dialogue_id", 101),
            "meal_periods": [planning.get("meal_period", "晚餐")],
            "diner_count": diner_count,
            "total_dish_count": None,
            "max_total_time_minutes": None,
            "max_difficulty": None,
            "available_ingredients": [],
            "allergens": [],
            "dishes": [
                {
                    "count": None,
                    "dish_type": "未指定",
                    "taste_preferences": {},
                    "cuisines": [],
                    "effects": [],
                    "special_populations": (
                        [] if index == 0 else [f"测试组{index}"]
                    ),
                    "required_ingredient_groups": [],
                }
                for index, _ in enumerate(groups)
            ],
            "has_conflicts": False,
            "conflicts": [],
        },
        "candidate_attempts": [
            {
                "candidate_limit": limit,
                "candidate_counts": (
                    counts if limit is None else staged_counts
                ),
                "outcome": "accepted",
                "nutrition_score": score,
            }
        ],
    }
