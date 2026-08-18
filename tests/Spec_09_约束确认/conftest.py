from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from .spec09_support import FakeMealPeriodService, FakeMultiTurnService


TEST_DIR = Path(__file__).resolve().parent
REPO_ROOT = TEST_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="session")
def production_contract():
    try:
        module = importlib.import_module(
            "backend.services.constraint_confirmation"
        )
        service_type = module.ConstraintConfirmationService
        error_type = module.ConstraintConfirmationError
    except (ModuleNotFoundError, AttributeError) as exc:
        pytest.fail(
            "红阶段缺少预期生产接口："
            "backend.services.constraint_confirmation."
            "ConstraintConfirmationService / ConstraintConfirmationError；"
            f"原始错误：{exc}",
            pytrace=False,
        )
    return SimpleNamespace(
        ConstraintConfirmationService=service_type,
        ConstraintConfirmationError=error_type,
    )


@pytest.fixture
def build_service(production_contract):
    def build(*meal_responses: object):
        multi_turn = FakeMultiTurnService()
        meal_period = FakeMealPeriodService(*meal_responses)
        service = production_contract.ConstraintConfirmationService(
            multi_turn,
            meal_period,
        )
        return service, multi_turn, meal_period

    return build
