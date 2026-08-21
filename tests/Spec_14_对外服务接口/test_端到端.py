from __future__ import annotations

import json
import os
import tomllib
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from tests.graph_data_support import ensure_graph_data

REPO_ROOT = Path(__file__).resolve().parents[2]
USERS_PATH = (
    REPO_ROOT
    / "datas"
    / "processed"
    / "users"
    / "50个用户健康档案_归一化.json"
)
RECIPES_PATH = (
    REPO_ROOT
    / "datas"
    / "processed"
    / "Recipes"
    / "RecipeComplete.json"
)
INGREDIENTS_PATH = (
    REPO_ROOT
    / "datas"
    / "processed"
    / "Ingredients"
    / "Ingredients2Nutrition.csv"
)
DRI_PATH = REPO_ROOT / "datas" / "processed" / "Nutrition" / "DRI2023.csv"

LLM_ENVIRONMENT_NAMES = frozenset(
    {
        "LLM_PROVIDER",
        "LLM_BASE_URL",
        "LLM_AUTH_TOKEN",
        "LLM_MODEL",
        "LLM_PROVIDER_BACKUP",
        "LLM_BASE_URL_BACKUP",
        "LLM_AUTH_TOKEN_BACKUP",
        "LLM_MODEL_BACKUP",
    }
)


def _load_dotenv() -> None:
    """加载环境；LLM配置以.env为准，其他配置保留进程优先级。"""

    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        raise AssertionError("真实端到端测试需要仓库根目录下的.env")
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        normalized_name = name.strip()
        normalized_value = value.strip()
        if normalized_name in LLM_ENVIRONMENT_NAMES:
            os.environ[normalized_name] = normalized_value
        else:
            os.environ.setdefault(normalized_name, normalized_value)


def _load_project_config() -> dict[str, Any]:
    with (REPO_ROOT / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["tool"]["mealagent"]


def _validated_test_database_url(config: dict[str, Any]) -> str:
    test_config = config["test_database"]
    database_url = test_config["url"]
    required_database = test_config["required_database"]
    parsed_url = make_url(database_url)
    if (
        not parsed_url.drivername.startswith("postgresql")
        or parsed_url.database != required_database
    ):
        raise pytest.UsageError(
            f"端到端测试只允许重建隔离测试库{required_database}"
        )
    return database_url


def _fixed_clock() -> datetime:
    """未明确餐次时固定按上海午餐窗口解析。"""

    return datetime(2026, 8, 19, 12, 0)


@contextmanager
def _create_test_environment() -> Iterator[Any]:
    """重建隔离测试库，并创建与生产组装方式一致的服务容器。"""

    from backend.application import ConstraintServices
    from backend.infrastructure.database import create_session_factory
    from backend.infrastructure.database.importer import import_basic_data
    from backend.infrastructure.database.models import Base
    from backend.infrastructure.graph import create_neo4j_driver
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
    from backend.services.meal_period_resolution import (
        MealPeriodResolutionService,
    )

    config = _load_project_config()
    engine = create_engine(
        _validated_test_database_url(config),
        pool_pre_ping=True,
    )
    graph_config = config["test_neo4j"]
    graph_driver = create_neo4j_driver(
        graph_config["uri"],
        graph_config["user"],
        graph_config["password"],
    )
    try:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            import_basic_data(
                RECIPES_PATH,
                INGREDIENTS_PATH,
                USERS_PATH,
                DRI_PATH,
                session,
            )
        session_factory = create_session_factory(engine)
        llm_client = create_langchain_constraint_extractor_from_environment()
        meal_period_service = MealPeriodResolutionService(
            clock=_fixed_clock,
            timezone_name="Asia/Shanghai",
        )
        dialogue_service = DialogueConstraintService(
            session_factory,
            llm_client,
            meal_period_service,
        )
        profile_service = ProfileConstraintService(session_factory)
        filtering_service = DishFilteringService(graph_driver)
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
        services = ConstraintServices(
            engine=engine,
            neo4j_driver=graph_driver,
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
        yield services
    finally:
        engine.dispose()
        graph_driver.close()


def parse_sse(response: Any) -> list[dict[str, Any]]:
    """解析SSE响应体为块列表。"""

    lines = [
        line
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    return [json.loads(line[6:]) for line in lines]


@pytest.mark.integration
def test_真实HTTP链路首轮自动建会话多轮延续并流式返回() -> None:
    """真实PG+Neo4j+LLM：首轮带档案自动建会话，多轮按会话号延续，流式SSE返回。"""

    from backend.api.app import create_app

    _load_dotenv()
    ensure_graph_data()
    with _create_test_environment() as services:
        with TestClient(create_app(services=services)) as client:
            session_id = None
            content = None
            for attempt in range(1, 6):
                try:
                    first = client.post(
                        "/v1/chat/completions",
                        json={
                            "profile_id": 25,
                            "messages": [
                                {"role": "user", "content": "帮我想顿晚饭"}
                            ],
                        },
                    )
                    assert first.status_code == 200
                    first_body = first.json()
                    assert first_body["status"] == "recommended"
                    content = first_body["choices"][0]["message"]["content"]
                    assert content.strip()
                    assert "晚餐" in content
                    session_id = first_body["session_id"]
                    break
                except AssertionError:
                    if attempt == 5:
                        raise

            second = client.post(
                "/v1/chat/completions",
                json={
                    "session_id": session_id,
                    "messages": [{"role": "user", "content": "别做辣的"}],
                },
            )
            assert second.status_code == 200
            second_body = second.json()
            assert second_body["session_id"] == session_id
            assert second_body["choices"][0]["message"]["content"].strip()

            streamed = client.post(
                "/v1/chat/completions",
                json={
                    "session_id": session_id,
                    "messages": [{"role": "user", "content": "帮我安排晚饭"}],
                    "stream": True,
                },
                headers={"Accept": "text/event-stream"},
            )
            assert streamed.status_code == 200
            assert streamed.headers["x-session-id"] == str(session_id)
            chunks = parse_sse(streamed)
            assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
            content_chunks = [
                chunk
                for chunk in chunks
                if chunk["choices"][0]["delta"].get("content")
            ]
            assert content_chunks
            assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
