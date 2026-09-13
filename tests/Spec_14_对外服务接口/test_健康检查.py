from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Callable

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.infrastructure import health as health_infrastructure
from backend.services.health_check import HealthCheckService, LlmHealthTarget

from .conftest import FakeConfirmationService, FakeRecommendationService


@dataclass
class _FakeHealthService:
    result: dict[str, object]

    def check(self) -> dict[str, object]:
        return self.result


def _successful_probe() -> None:
    return None


def _failed_probe() -> None:
    raise RuntimeError("包含 sk-secret 和 https://private.example 的上游错误")


def _build_health_service(
    *,
    postgresql_probe: Callable[[], object] = _successful_probe,
    postgresql_data_probe: Callable[[], object] = _successful_probe,
    neo4j_probe: Callable[[], object] = _successful_probe,
    neo4j_data_probe: Callable[[], object] = _successful_probe,
    primary_probe: Callable[[], object] = _successful_probe,
    backup_probe: Callable[[], object] = _successful_probe,
) -> HealthCheckService:
    return HealthCheckService(
        postgresql_probe=postgresql_probe,
        postgresql_data_probe=postgresql_data_probe,
        neo4j_probe=neo4j_probe,
        neo4j_data_probe=neo4j_data_probe,
        primary_llm=LlmHealthTarget("qwen3.8-max", primary_probe),
        backup_llm=LlmHealthTarget("deepseek-v4-flash", backup_probe),
    )


def _build_client(
    result: dict[str, object],
    api_token: str | None = None,
) -> TestClient:
    services = SimpleNamespace(
        confirmation=FakeConfirmationService(),
        recommendation=FakeRecommendationService(),
        health=_FakeHealthService(result),
    )
    return TestClient(create_app(services=services, api_token=api_token))


def test_完整健康检查全部成功() -> None:
    result = _build_health_service().check()

    assert result["status"] == "ok"
    assert result["available_models"] == ["qwen3.8-max", "deepseek-v4-flash"]
    assert set(result["checks"]) == {
        "postgresql",
        "postgresql_data",
        "neo4j",
        "neo4j_data",
        "llm_primary",
        "llm_backup",
    }
    assert all(
        check["status"] == "ok"
        for check in result["checks"].values()
    )


def test_单个LLM失败时降级但仍可用() -> None:
    result = _build_health_service(backup_probe=_failed_probe).check()

    assert result["status"] == "degraded"
    assert result["available_models"] == ["qwen3.8-max"]
    assert result["checks"]["llm_backup"]["status"] == "error"


def test_两个LLM均失败时不健康() -> None:
    result = _build_health_service(
        primary_probe=_failed_probe,
        backup_probe=_failed_probe,
    ).check()

    assert result["status"] == "unhealthy"
    assert result["available_models"] == []


def test_数据库或必需数据失败时不健康() -> None:
    result = _build_health_service(
        postgresql_data_probe=_failed_probe,
    ).check()

    assert result["status"] == "unhealthy"
    assert result["checks"]["postgresql_data"]["error_code"] == "data_unavailable"


def test_健康检查不泄露上游错误详情() -> None:
    result = _build_health_service(primary_probe=_failed_probe).check()

    serialized = str(result)
    assert "sk-secret" not in serialized
    assert "private.example" not in serialized
    assert result["checks"]["llm_primary"]["error_code"] == "llm_unavailable"


def test_健康接口按结果返回200或503() -> None:
    healthy = _build_health_service().check()
    unhealthy = _build_health_service(
        neo4j_probe=_failed_probe,
    ).check()

    with _build_client(healthy) as client:
        healthy_response = client.get("/health")
    with _build_client(unhealthy) as client:
        unhealthy_response = client.get("/health")

    assert healthy_response.status_code == 200
    assert healthy_response.headers["cache-control"] == "no-store"
    assert unhealthy_response.status_code == 503
    assert unhealthy_response.json()["status"] == "unhealthy"


def test_启用鉴权时健康接口未带密钥返回401() -> None:
    with _build_client(_build_health_service().check(), "secret") as client:
        response = client.get("/health")

    assert response.status_code == 401


def test_启用鉴权时健康接口带密钥放行() -> None:
    with _build_client(_build_health_service().check(), "secret") as client:
        response = client.get(
            "/health",
            headers={"Authorization": "Bearer secret"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_存活检查免密钥() -> None:
    with _build_client(_build_health_service().check(), "secret") as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


class _FakeDatabaseResult:
    def __init__(self, invalid_count: int = 0) -> None:
        self.invalid_count = invalid_count

    def first(self):
        return object()

    def scalar_one(self) -> int:
        return self.invalid_count


class _FakeDatabaseSession:
    def __init__(self, invalid_count: int) -> None:
        self.invalid_count = invalid_count
        self.statements: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def execute(self, statement):
        text = str(statement)
        self.statements.append(text)
        if "is_staple_component" in text:
            return _FakeDatabaseResult(self.invalid_count)
        return _FakeDatabaseResult()

    def scalar(self, statement) -> int:
        return self.execute(statement).scalar_one()


def test_PostgreSQL健康检查验证主食布尔字段完整(monkeypatch) -> None:
    fake_session = _FakeDatabaseSession(invalid_count=0)
    monkeypatch.setattr(
        health_infrastructure,
        "Session",
        lambda engine: fake_session,
    )

    health_infrastructure.check_postgresql_data(object())

    assert any(
        "is_staple_component" in statement
        for statement in fake_session.statements
    )


def test_PostgreSQL存在空主食布尔值时健康检查失败(monkeypatch) -> None:
    fake_session = _FakeDatabaseSession(invalid_count=1)
    monkeypatch.setattr(
        health_infrastructure,
        "Session",
        lambda engine: fake_session,
    )

    with pytest.raises(RuntimeError, match="主食|is_staple_component"):
        health_infrastructure.check_postgresql_data(object())


class _FakeGraphRecord(dict):
    pass


class _FakeGraphSession:
    def __init__(self, invalid_count: int) -> None:
        self.invalid_count = invalid_count
        self.queries: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def run(self, query: str):
        self.queries.append(query)
        return SimpleNamespace(
            single=lambda: _FakeGraphRecord(
                has_recipe=True,
                has_ingredient=True,
                has_concept=True,
                has_part_of=True,
                has_is_a=True,
                invalid_staple_relation_count=self.invalid_count,
            )
        )


class _FakeGraphDriver:
    def __init__(self, invalid_count: int) -> None:
        self.graph_session = _FakeGraphSession(invalid_count)

    def session(self):
        return self.graph_session


def test_Neo4j健康检查验证全部part_of主食布尔属性() -> None:
    driver = _FakeGraphDriver(invalid_count=0)

    health_infrastructure.check_neo4j_data(driver)

    assert "is_staple_component" in "\n".join(driver.graph_session.queries)


@pytest.mark.parametrize("invalid_count", [1, 2])
def test_Neo4j有缺失或非布尔主食属性时健康检查失败(
    invalid_count,
) -> None:
    driver = _FakeGraphDriver(invalid_count=invalid_count)

    with pytest.raises(RuntimeError, match="主食|is_staple_component"):
        health_infrastructure.check_neo4j_data(driver)
