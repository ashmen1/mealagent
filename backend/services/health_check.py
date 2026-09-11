from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any


HealthProbe = Callable[[], object]


@dataclass(frozen=True)
class LlmHealthTarget:
    """一个需要独立探测的LLM及其公开模型名。"""

    model_name: str
    probe: HealthProbe


class HealthCheckService:
    """并行检查应用依赖并计算整体健康状态。"""

    def __init__(
        self,
        postgresql_probe: HealthProbe,
        postgresql_data_probe: HealthProbe,
        neo4j_probe: HealthProbe,
        neo4j_data_probe: HealthProbe,
        primary_llm: LlmHealthTarget,
        backup_llm: LlmHealthTarget | None,
    ) -> None:
        self._dependency_probes = {
            "postgresql": postgresql_probe,
            "postgresql_data": postgresql_data_probe,
            "neo4j": neo4j_probe,
            "neo4j_data": neo4j_data_probe,
        }
        self._primary_llm = primary_llm
        self._backup_llm = backup_llm

    def check(self) -> dict[str, Any]:
        """执行完整检查；结果不包含上游异常文本或连接信息。"""

        started_at = perf_counter()
        checks: dict[str, dict[str, Any]] = {}
        jobs: list[tuple[str, HealthProbe, str, str | None]] = [
            (
                name,
                probe,
                (
                    "data_unavailable"
                    if name.endswith("_data")
                    else "dependency_unavailable"
                ),
                None,
            )
            for name, probe in self._dependency_probes.items()
        ]
        jobs.append(
            (
                "llm_primary",
                self._primary_llm.probe,
                "llm_unavailable",
                self._primary_llm.model_name,
            )
        )
        if self._backup_llm is not None:
            jobs.append(
                (
                    "llm_backup",
                    self._backup_llm.probe,
                    "llm_unavailable",
                    self._backup_llm.model_name,
                )
            )

        with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
            futures = {
                name: executor.submit(
                    _run_probe,
                    probe,
                    error_code,
                    model_name,
                )
                for name, probe, error_code, model_name in jobs
            }
            for name, future in futures.items():
                checks[name] = future.result()

        if self._backup_llm is None:
            checks["llm_backup"] = {
                "status": "error",
                "duration_ms": 0,
                "error_code": "configuration_missing",
            }

        available_models = [
            target.model_name
            for check_name, target in (
                ("llm_primary", self._primary_llm),
                ("llm_backup", self._backup_llm),
            )
            if target is not None and checks[check_name]["status"] == "ok"
        ]
        dependencies_are_healthy = all(
            checks[name]["status"] == "ok"
            for name in self._dependency_probes
        )
        if not dependencies_are_healthy or not available_models:
            status = "unhealthy"
        elif len(available_models) == 1:
            status = "degraded"
        else:
            status = "ok"

        return {
            "status": status,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": _elapsed_ms(started_at),
            "available_models": available_models,
            "checks": checks,
        }


def _run_probe(
    probe: HealthProbe,
    error_code: str,
    model_name: str | None,
) -> dict[str, Any]:
    started_at = perf_counter()
    result: dict[str, Any] = {
        "status": "ok",
        "duration_ms": 0,
    }
    if model_name is not None:
        result["model"] = model_name
    try:
        probe()
    except Exception:
        result["status"] = "error"
        result["error_code"] = error_code
    result["duration_ms"] = _elapsed_ms(started_at)
    return result


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))


__all__ = ["HealthCheckService", "LlmHealthTarget"]
