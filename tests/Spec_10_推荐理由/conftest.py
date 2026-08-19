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
    ) -> dict[str, Any]:
        service = production_contract.RecommendationReasonService()
        return service.build(dish_filtering_result, menu_planning_result)

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
