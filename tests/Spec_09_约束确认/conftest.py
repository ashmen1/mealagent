from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from .spec09_support import FakeMealPeriodService, FakeMultiTurnService


TEST_DIR = Path(__file__).resolve().parent
REPO_ROOT = TEST_DIR.parents[1]
repo_root_text = str(REPO_ROOT)
if repo_root_text not in sys.path:
    sys.path.insert(0, repo_root_text)


@pytest.fixture(scope="session")
def production_contract():
    try:
        module = importlib.import_module(
            "backend.services.constraint_confirmation"
        )
    except (ModuleNotFoundError, AttributeError) as exc:
        pytest.fail(
            "缺少约束确认生产接口："
            "backend.services.constraint_confirmation."
            "ConstraintConfirmationService 或 ConstraintConfirmationError；"
            f"原始错误：{exc}",
            pytrace=False,
        )
    return SimpleNamespace(
        ConstraintConfirmationService=module.ConstraintConfirmationService,
        ConstraintConfirmationError=module.ConstraintConfirmationError,
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
