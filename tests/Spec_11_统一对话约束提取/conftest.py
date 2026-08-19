from __future__ import annotations

import importlib
import sys
import tomllib
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


SPEC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPEC_DIR.parents[1]
repo_root_text = str(REPO_ROOT)
if repo_root_text not in sys.path:
    sys.path.insert(0, repo_root_text)

from backend.infrastructure.database import create_session_factory

from .spec11_support import FakeLLMClient


@pytest.fixture(scope="session")
def production_contract():
    service_module = importlib.import_module(
        "backend.services.dialogue_constraints"
    )
    prompt_module = importlib.import_module(
        "backend.services.dialogue_constraint_prompt"
    )
    contract_module = importlib.import_module(
        "backend.core.dialogue_constraint_contract"
    )
    models_module = importlib.import_module(
        "backend.infrastructure.database.models"
    )
    meal_module = importlib.import_module(
        "backend.services.meal_period_resolution"
    )
    adapter_module = importlib.import_module(
        "backend.infrastructure.llm.langchain_constraints"
    )
    services_module = importlib.import_module("backend.services")
    llm_module = importlib.import_module("backend.infrastructure.llm")

    return SimpleNamespace(
        DialogueConstraintService=service_module.DialogueConstraintService,
        DialogueConstraintExtractionError=(
            service_module.DialogueConstraintExtractionError
        ),
        build_prompt=prompt_module.build_dialogue_prompt,
        output_schema=contract_module.CONSTRAINT_OUTPUT_SCHEMA,
        services_module=services_module,
        llm_module=llm_module,
        LangChainConstraintExtractor=(
            adapter_module.LangChainConstraintExtractor
        ),
        Base=models_module.Base,
        DialogueSession=models_module.DialogueSession,
        DialogueTurn=models_module.DialogueTurn,
        Ingredient=models_module.Ingredient,
        UserProfile=models_module.UserProfile,
        MealPeriodResolutionService=meal_module.MealPeriodResolutionService,
    )


def load_test_database_url() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as stream:
        project_config = tomllib.load(stream)
    config = project_config["tool"]["mealagent"]["test_database"]
    database_url = config["url"]
    required_database = config["required_database"]
    parsed_url = make_url(database_url)
    if (
        not parsed_url.drivername.startswith("postgresql")
        or parsed_url.database != required_database
    ):
        raise pytest.UsageError(
            f"Spec_11测试只允许连接{required_database}"
        )
    return database_url


@pytest.fixture
def db_engine(production_contract):
    engine = create_engine(load_test_database_url(), pool_pre_ping=True)
    production_contract.Base.metadata.drop_all(engine)
    production_contract.Base.metadata.create_all(engine)
    yield engine
    production_contract.Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    with Session(db_engine) as session:
        yield session
        session.rollback()


@pytest.fixture
def session_factory(db_engine):
    return create_session_factory(db_engine)


@pytest.fixture
def profile_id(db_session, production_contract):
    profile = production_contract.UserProfile(
        id=91001,
        sex="男",
        age=30,
        activity_level="低",
        special_populations=[],
        gestational_week=None,
        is_menstruating=None,
        taste_preference="清淡",
        allergens=[],
        health_goals=[],
        height_cm=Decimal("175.0"),
        weight_kg=Decimal("70.0"),
        bmi=Decimal("22.86"),
        medical_metrics={},
    )
    db_session.add(profile)
    db_session.commit()
    return profile.id


@pytest.fixture
def seed_ingredients(db_session, production_contract):
    db_session.add_all(
        [
            production_contract.Ingredient(
                id=1, name="番茄", category="蔬菜", aliases=[]
            ),
            production_contract.Ingredient(
                id=2, name="鸡蛋", category="蛋奶", aliases=[]
            ),
            production_contract.Ingredient(
                id=3, name="土豆", category="蔬菜", aliases=[]
            ),
            production_contract.Ingredient(
                id=4, name="鱼", category="水产", aliases=[]
            ),
            production_contract.Ingredient(
                id=5, name="鸡翅", category="禽肉", aliases=[]
            ),
            production_contract.Ingredient(
                id=6, name="米饭", category="粮食", aliases=[]
            ),
        ]
    )
    db_session.commit()


@pytest.fixture
def clock_at() -> Callable[[int, int], Callable[[], datetime]]:
    def build(hour: int, minute: int = 0) -> Callable[[], datetime]:
        return lambda: datetime(2026, 8, 19, hour, minute)

    return build


@pytest.fixture
def build_service(production_contract, clock_at):
    def build(
        session_factory: Callable[[], Session],
        llm_client: Callable[[str], object],
        clock: Callable[[], datetime] | None = None,
    ):
        resolver = production_contract.MealPeriodResolutionService(
            clock=clock or clock_at(12, 0)
        )
        return production_contract.DialogueConstraintService(
            session_factory,
            llm_client,
            resolver,
        )

    return build


@pytest.fixture
def start_session(
    build_service,
    session_factory,
    profile_id,
    seed_ingredients,
):
    def start(*, clock=None):
        llm_client = FakeLLMClient()
        service = build_service(session_factory, llm_client, clock)
        session_id = service.create_session(profile_id)
        return service, llm_client, session_id

    return start


@pytest.fixture
def assert_dialogue_error(production_contract):
    def assert_error(action: Callable[[], object], status_code: int):
        with pytest.raises(
            production_contract.DialogueConstraintExtractionError
        ) as captured:
            action()
        assert captured.value.status_code == status_code
        return captured.value

    return assert_error
