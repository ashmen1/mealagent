from __future__ import annotations

import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.engine import Engine

from backend.infrastructure.database import (
    create_database_engine,
    create_session_factory,
)
from backend.infrastructure.graph import (
    GraphConfigurationError,
    create_neo4j_driver,
)
from backend.infrastructure.llm import (
    create_langchain_constraint_extractor_from_environment,
)
from backend.services import (
    ConstraintConfirmationService,
    ConstraintIntegrationService,
    DialogueConstraintService,
    DishFilteringService,
    MenuPlanningService,
    MenuRecommendationService,
    NutritionService,
    ProfileConstraintService,
    RecommendationReasonService,
)
from backend.services.meal_period_resolution import MealPeriodResolutionService


PYPROJECT_PATH = Path(__file__).resolve().parents[1] / "pyproject.toml"


class ApplicationConfigurationError(ValueError):
    """应用运行配置不完整或不合法。"""

    status_code = 500


class ConstraintServices:
    """统一持有约束提取Service及其共享基础设施。"""

    def __init__(
        self,
        engine: Engine,
        neo4j_driver: Any,
        profile: ProfileConstraintService,
        dialogue: DialogueConstraintService,
        dish_filtering: DishFilteringService,
        confirmation: ConstraintConfirmationService,
        integration: ConstraintIntegrationService,
        nutrition: NutritionService,
        menu_planning: MenuPlanningService,
        recommendation_reason: RecommendationReasonService,
        recommendation: MenuRecommendationService,
    ) -> None:
        self._engine = engine
        self._neo4j_driver = neo4j_driver
        self.profile = profile
        self.dialogue = dialogue
        self.dish_filtering = dish_filtering
        self.confirmation = confirmation
        self.integration = integration
        self.nutrition = nutrition
        self.menu_planning = menu_planning
        self.recommendation_reason = recommendation_reason
        self.recommendation = recommendation
        self._is_closed = False

    def __enter__(self) -> ConstraintServices:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        """释放应用容器持有的数据库Engine与Neo4j Driver。"""

        if self._is_closed:
            return
        self._engine.dispose()
        self._neo4j_driver.close()
        self._is_closed = True


def create_constraint_services() -> ConstraintServices:
    """从项目配置创建可长期复用的约束提取Service。"""

    database_url = _load_database_url(PYPROJECT_PATH)
    neo4j_config = _load_neo4j_config(PYPROJECT_PATH)
    engine = create_database_engine(database_url)
    neo4j_driver = create_neo4j_driver(
        neo4j_config["uri"],
        neo4j_config["user"],
        neo4j_config["password"],
    )
    try:
        session_factory = create_session_factory(engine)
        llm_client = create_langchain_constraint_extractor_from_environment()
        meal_period_service = MealPeriodResolutionService(
            clock=_business_clock
        )
        dialogue_service = DialogueConstraintService(
            session_factory,
            llm_client,
            meal_period_service,
        )
        profile_service = ProfileConstraintService(session_factory)
        filtering_service = DishFilteringService(neo4j_driver)
        confirmation_service = ConstraintConfirmationService(
            dialogue_service,
            meal_period_service,
        )
        integration_service = ConstraintIntegrationService()
        nutrition_service = NutritionService(session_factory)
        planning_service = MenuPlanningService()
        reason_service = RecommendationReasonService()
        recommendation_service = MenuRecommendationService(
            confirmation_service=confirmation_service,
            profile_service=profile_service,
            integration_service=integration_service,
            filtering_service=filtering_service,
            nutrition_service=nutrition_service,
            planning_service=planning_service,
            reason_service=reason_service,
        )
        return ConstraintServices(
            engine=engine,
            neo4j_driver=neo4j_driver,
            profile=profile_service,
            dialogue=dialogue_service,
            dish_filtering=filtering_service,
            confirmation=confirmation_service,
            integration=integration_service,
            nutrition=nutrition_service,
            menu_planning=planning_service,
            recommendation_reason=reason_service,
            recommendation=recommendation_service,
        )
    except BaseException:
        engine.dispose()
        neo4j_driver.close()
        raise


def _business_clock() -> datetime:
    """业务时区(Asia/Shanghai)的当前时间,供餐次解析使用。"""

    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _load_database_url(pyproject_path: Path) -> str:
    try:
        with pyproject_path.open("rb") as stream:
            project_config = tomllib.load(stream)
        database_url = project_config["tool"]["mealagent"]["database"]["url"]
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise ApplicationConfigurationError(
            "pyproject.toml缺少有效的tool.mealagent.database.url配置"
        ) from exc

    if not isinstance(database_url, str) or not database_url.strip():
        raise ApplicationConfigurationError("业务数据库URL必须是非空字符串")
    return database_url.strip()


def _load_neo4j_config(pyproject_path: Path) -> dict[str, str]:
    try:
        with pyproject_path.open("rb") as stream:
            project_config = tomllib.load(stream)
        neo4j_config = project_config["tool"]["mealagent"]["neo4j"]
        config = {
            key: neo4j_config[key]
            for key in ("uri", "user", "password")
        }
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise ApplicationConfigurationError(
            "pyproject.toml缺少有效的tool.mealagent.neo4j配置"
        ) from exc

    if any(
        not isinstance(value, str) or not value.strip()
        for value in config.values()
    ):
        raise ApplicationConfigurationError(
            "Neo4j配置的uri、user、password必须是非空字符串"
        )
    return config


__all__ = [
    "ApplicationConfigurationError",
    "ConstraintServices",
    "create_constraint_services",
]
