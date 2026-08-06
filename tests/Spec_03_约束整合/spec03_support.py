from __future__ import annotations

import copy
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest


SPEC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPEC_DIR.parents[1]
repo_root_text = str(REPO_ROOT)
if repo_root_text not in sys.path:
    sys.path.insert(0, repo_root_text)


def build_profile_constraints(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "profile_id": 25,
        "special_populations": [],
        "taste_preferences": {},
        "allergens": [],
    }
    values.update(copy.deepcopy(overrides))
    return values


def build_dish(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "count": None,
        "dish_type": "未指定",
        "taste_preferences": {},
        "cuisines": [],
        "effects": [],
        "special_populations": [],
        "required_ingredients": [],
    }
    values.update(copy.deepcopy(overrides))
    return values


def build_dialogue_constraints(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "dialogue_id": 1,
        "meal_periods": [],
        "diner_count": None,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [build_dish()],
        "evidence": {},
    }
    values.update(copy.deepcopy(overrides))
    return values


@pytest.fixture
def production_contract():
    try:
        module = importlib.import_module(
            "backend.services.constraint_integration"
        )
        integration_service = module.ConstraintIntegrationService
    except (ModuleNotFoundError, AttributeError) as exc:
        pytest.fail(
            "缺少 Spec_03 约定的生产接口："
            "backend.services.constraint_integration."
            "ConstraintIntegrationService；"
            f"原始错误：{exc}",
            pytrace=False,
        )

    return SimpleNamespace(
        ConstraintIntegrationService=integration_service,
    )


@pytest.fixture
def invoke_integrate(production_contract) -> Callable[..., dict[str, Any]]:
    def invoke(
        profile_constraints: dict[str, Any],
        dialogue_constraints: dict[str, Any],
    ) -> dict[str, Any]:
        service = production_contract.ConstraintIntegrationService()
        return service.integrate(profile_constraints, dialogue_constraints)

    return invoke


@pytest.fixture
def assert_integration_error(invoke_integrate):
    def assert_error(
        profile_constraints: dict[str, Any],
        dialogue_constraints: dict[str, Any],
    ) -> Exception:
        with pytest.raises(Exception) as captured:
            invoke_integrate(profile_constraints, dialogue_constraints)
        assert getattr(captured.value, "status_code", None) == 400
        return captured.value

    return assert_error

