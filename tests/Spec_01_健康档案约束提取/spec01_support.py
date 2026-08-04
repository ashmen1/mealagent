from __future__ import annotations

import copy
import importlib
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest


SPEC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPEC_DIR.parents[1]
repo_root_text = str(REPO_ROOT)
if repo_root_text not in sys.path:
    sys.path.insert(0, repo_root_text)

from backend.storage.models import UserProfile


@pytest.fixture
def production_contract():
    try:
        module = importlib.import_module("backend.profile_constraints")
        extract_profile_constraints = module.extract_profile_constraints
        validation_error = module.ProfileConstraintValidationError
    except (ModuleNotFoundError, AttributeError) as exc:
        pytest.fail(
            "缺少 Spec_01 约定的生产接口："
            "backend.profile_constraints.extract_profile_constraints 或 "
            "ProfileConstraintValidationError；"
            f"原始错误：{exc}",
            pytrace=False,
        )

    return SimpleNamespace(
        extract_profile_constraints=extract_profile_constraints,
        ProfileConstraintValidationError=validation_error,
    )


@pytest.fixture
def profile_factory() -> Callable[..., UserProfile]:
    def create_profile(**overrides: Any) -> UserProfile:
        values: dict[str, Any] = {
            "id": 25,
            "sex": "女",
            "age": 30,
            "activity_level": "中",
            "special_populations": [],
            "gestational_week": None,
            "taste_preference": "清淡",
            "allergens": [],
            "health_goals": [],
            "height_cm": Decimal("165.0"),
            "weight_kg": Decimal("55.0"),
            "bmi": Decimal("20.2"),
            "medical_metrics": {},
        }
        values.update(copy.deepcopy(overrides))
        return UserProfile(**values)

    return create_profile


@pytest.fixture
def invoke_extract(production_contract):
    def invoke(profile: UserProfile) -> dict[str, Any]:
        return production_contract.extract_profile_constraints(profile)

    return invoke


@pytest.fixture
def assert_validation_error(production_contract, invoke_extract):
    def assert_error(profile: UserProfile):
        with pytest.raises(
            production_contract.ProfileConstraintValidationError
        ) as captured:
            invoke_extract(profile)
        assert captured.value.status_code == 400
        return captured.value

    return assert_error
