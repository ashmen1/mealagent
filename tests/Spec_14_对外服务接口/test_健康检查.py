from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Callable

from fastapi.testclient import TestClient

from backend.api.app import create_app
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


def _build_client(result: dict[str, object]) -> TestClient:
    services = SimpleNamespace(
        confirmation=FakeConfirmationService(),
        recommendation=FakeRecommendationService(),
        health=_FakeHealthService(result),
    )
    return TestClient(create_app(services=services))


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
