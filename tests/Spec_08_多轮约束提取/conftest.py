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

from .spec08_support import FakeLLMClient


@pytest.fixture(scope="session")
def production_contract():
    try:
        service_module = importlib.import_module(
            "backend.services.multi_turn_constraints"
        )
        models_module = importlib.import_module(
            "backend.infrastructure.database.models"
        )
        meal_module = importlib.import_module(
            "backend.services.meal_period_resolution"
        )
        multi_turn_adapter = importlib.import_module(
            "backend.infrastructure.llm.langchain_multi_turn"
        )
    except (ModuleNotFoundError, AttributeError) as exc:
        pytest.fail(
            "缺少 Spec_08 约定的生产接口："
            "backend.services.multi_turn_constraints."
            "MultiTurnConstraintService 或 MultiTurnConstraintError，"
            "backend.infrastructure.database.models."
            "DialogueSession / DialogueTurn，或 "
            "backend.services.meal_period_resolution."
            "MealPeriodResolutionService；"
            f"原始错误：{exc}",
            pytrace=False,
        )

    return SimpleNamespace(
        MultiTurnConstraintService=service_module.MultiTurnConstraintService,
        MultiTurnConstraintError=service_module.MultiTurnConstraintError,
        Base=models_module.Base,
        DialogueSession=models_module.DialogueSession,
        DialogueTurn=models_module.DialogueTurn,
        Ingredient=models_module.Ingredient,
        UserProfile=models_module.UserProfile,
        MealPeriodResolutionService=meal_module.MealPeriodResolutionService,
        create_langchain_multi_turn_extractor_from_environment=(
            multi_turn_adapter.create_langchain_multi_turn_extractor_from_environment
        ),
    )


@pytest.fixture
def db_engine(production_contract):
    database_url = load_test_database_url()
    engine = create_engine(database_url, pool_pre_ping=True)

    production_contract.Base.metadata.drop_all(engine)
    production_contract.Base.metadata.create_all(engine)
    yield engine
    production_contract.Base.metadata.drop_all(engine)
    engine.dispose()


def load_test_database_url() -> str:
    pyproject_path = REPO_ROOT / "pyproject.toml"
    with pyproject_path.open("rb") as stream:
        project_config = tomllib.load(stream)

    try:
        test_database = project_config["tool"]["mealagent"]["test_database"]
        database_url = test_database["url"]
        required_database = test_database["required_database"]
    except (KeyError, TypeError) as exc:
        raise pytest.UsageError(
            "pyproject.toml 缺少 tool.mealagent.test_database 配置"
        ) from exc

    if not isinstance(database_url, str) or not database_url.strip():
        raise pytest.UsageError("测试数据库 URL 必须是非空字符串")
    if not isinstance(required_database, str) or not required_database.strip():
        raise pytest.UsageError("测试数据库名必须是非空字符串")

    parsed_url = make_url(database_url.strip())
    if not parsed_url.drivername.startswith("postgresql"):
        raise pytest.UsageError("完整 Spec_08 必须使用 PostgreSQL 测试库")
    if parsed_url.database != required_database.strip():
        raise pytest.UsageError(
            f"测试只允许连接 {required_database.strip()}"
        )
    return database_url.strip()


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
    """插入一条最小合法用户档案并返回其id。"""

    profile = production_contract.UserProfile(
        id=90001,
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
def clock_at() -> Callable[..., Callable[[], datetime]]:
    """构造固定时钟：返回指定的上海本地时间,用于验证时间窗口判断。"""

    def clock_at_impl(
        hour: int,
        minute: int = 0,
        second: int = 0,
        microsecond: int = 0,
    ) -> Callable[[], datetime]:
        def clock() -> datetime:
            return datetime(2026, 8, 14, hour, minute, second, microsecond)

        return clock

    return clock_at_impl


@pytest.fixture
def build_service(production_contract, clock_at):
    def build(
        session_factory: Callable[[], Session],
        llm_client: Callable[[str], object],
        clock: Callable[[], datetime] | None = None,
    ):
        resolver_clock = clock if clock is not None else clock_at(12, 0)
        resolver = production_contract.MealPeriodResolutionService(
            clock=resolver_clock
        )
        return production_contract.MultiTurnConstraintService(
            session_factory,
            llm_client,
            resolver,
        )

    return build


@pytest.fixture
def seed_ingredients(db_session, production_contract):
    """插入约束提取所需的少量标准食材。"""

    db_session.add_all(
        [
            production_contract.Ingredient(
                id=1,
                name="番茄",
                category="蔬菜",
                aliases=[],
            ),
            production_contract.Ingredient(
                id=2,
                name="鸡蛋",
                category="蛋奶",
                aliases=[],
            ),
            production_contract.Ingredient(
                id=3,
                name="土豆",
                category="蔬菜",
                aliases=[],
            ),
            production_contract.Ingredient(
                id=4,
                name="米饭",
                category="粮食",
                aliases=[],
            ),
            production_contract.Ingredient(
                id=5,
                name="鱼",
                category="水产",
                aliases=[],
            ),
            production_contract.Ingredient(
                id=6,
                name="鸡翅",
                category="禽肉",
                aliases=[],
            ),
        ]
    )
    db_session.commit()


@pytest.fixture
def start_session(build_service, session_factory, profile_id, seed_ingredients):
    """创建会话并返回(service, llm_client, session_id),便于脚本化多轮。"""

    def start():
        llm_client = FakeLLMClient()
        service = build_service(session_factory, llm_client)
        session_id = service.create_session(profile_id)
        return service, llm_client, session_id

    return start


@pytest.fixture
def assert_multi_turn_error(production_contract):
    def assert_error(action: Callable[[], object], expected_status_code: int):
        with pytest.raises(
            production_contract.MultiTurnConstraintError
        ) as captured:
            action()
        assert captured.value.status_code == expected_status_code
        return captured.value

    return assert_error
