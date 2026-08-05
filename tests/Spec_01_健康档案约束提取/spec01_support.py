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

from backend.infrastructure.database.models import UserProfile
from backend.infrastructure.database.profile_repository import (
    ProfileRepositoryError,
)


class RecordingSessionContext:
    def __init__(self) -> None:
        self.session = object()
        self.enter_count = 0
        self.exit_count = 0

    def __enter__(self):
        self.enter_count += 1
        return self.session

    def __exit__(self, *exc_info: object) -> None:
        self.exit_count += 1


class RecordingSessionFactory:
    def __init__(self) -> None:
        self.call_count = 0
        self.contexts: list[RecordingSessionContext] = []

    def __call__(self) -> RecordingSessionContext:
        self.call_count += 1
        context = RecordingSessionContext()
        self.contexts.append(context)
        return context


@pytest.fixture
def production_contract():
    try:
        module = importlib.import_module(
            "backend.services.profile_constraints"
        )
        profile_service = module.ProfileConstraintService
        extraction_error = module.ProfileConstraintExtractionError
        validation_error = module.ProfileConstraintValidationError
    except (ModuleNotFoundError, AttributeError) as exc:
        pytest.fail(
            "缺少 Spec_01 约定的生产接口："
            "backend.services.profile_constraints."
            "ProfileConstraintService、ProfileConstraintExtractionError 或 "
            "ProfileConstraintValidationError；"
            f"原始错误：{exc}",
            pytrace=False,
        )

    return SimpleNamespace(
        ProfileConstraintService=profile_service,
        ProfileConstraintExtractionError=extraction_error,
        ProfileConstraintValidationError=validation_error,
        ProfileRepositoryError=ProfileRepositoryError,
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
        session_factory = RecordingSessionFactory()

        def load_profile(session: object, profile_id: int) -> UserProfile:
            del session
            assert profile_id == profile.id
            return profile

        service = production_contract.ProfileConstraintService(
            session_factory,
            load_profile,
        )
        return service.extract(profile.id)

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
