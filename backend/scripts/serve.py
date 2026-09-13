"""启动本地对外服务：拉起基础设施、等待就绪、守护运行 uvicorn。

用法（在项目根目录执行）：
    uv --cache-dir .uv-cache run --no-dev --env-file .env python -m backend.scripts.serve

访问密钥从环境变量 MEALAGENT_API_TOKEN 读取，未配置时拒绝启动，
服务不会在无鉴权状态下被拉起。
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Final
from urllib.parse import urlparse

import uvicorn
from sqlalchemy.engine import make_url

from backend.api.app import create_app
from backend.application import (
    PYPROJECT_PATH,
    ApplicationConfigurationError,
    _load_database_url,
    _load_neo4j_config,
)


REPOSITORY_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
API_HOST: Final[str] = "127.0.0.1"
API_PORT: Final[int] = 8000
INFRASTRUCTURE_TIMEOUT_SECONDS: Final[float] = 120.0
RESTART_DELAY_SECONDS: Final[float] = 3.0
PROBE_TIMEOUT_SECONDS: Final[float] = 1.0
PROBE_INTERVAL_SECONDS: Final[float] = 0.5


class ServeStartupError(RuntimeError):
    """本地服务的启动前置条件不满足。"""


def main() -> int:
    """校验配置、拉起基础设施与依赖、守护运行HTTP服务。"""

    api_token = _load_api_token()
    _start_infrastructure()
    for label, host, port in _resolve_infrastructure_endpoints():
        print(f"等待 {label} 就绪：{host}:{port}")
        _wait_for_endpoint(label, host, port, INFRASTRUCTURE_TIMEOUT_SECONDS)
        print(f"{label} 已就绪")
    _ensure_port_available(API_HOST, API_PORT)
    print(f"启动HTTP服务：http://{API_HOST}:{API_PORT}")
    _serve(api_token)
    return 0


def _load_api_token() -> str:
    """读取访问密钥；未配置时直接失败，不使用空密钥继续启动。"""

    api_token = os.environ.get("MEALAGENT_API_TOKEN", "").strip()
    if not api_token:
        raise ServeStartupError(
            "环境变量 MEALAGENT_API_TOKEN 未配置，拒绝以无鉴权方式启动服务"
        )
    return api_token


def _start_infrastructure() -> None:
    """用docker compose拉起PostgreSQL与Neo4j容器。"""

    print("拉起基础设施：docker compose up -d")
    result = subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd=REPOSITORY_ROOT,
        check=False,
    )
    if result.returncode != 0:
        raise ServeStartupError(
            f"docker compose up -d 失败，退出码 {result.returncode}"
        )


def _resolve_infrastructure_endpoints() -> list[tuple[str, str, int]]:
    """从项目配置解析待探测的依赖地址，避免与pyproject.toml漂移。"""

    database_url = make_url(_load_database_url(PYPROJECT_PATH))
    neo4j_uri = urlparse(_load_neo4j_config(PYPROJECT_PATH)["uri"])
    endpoints: list[tuple[str, str, int]] = []
    for label, host, port in (
        ("PostgreSQL", database_url.host, database_url.port),
        ("Neo4j", neo4j_uri.hostname, neo4j_uri.port),
    ):
        if not host or port is None:
            raise ApplicationConfigurationError(
                f"{label}配置缺少可探测的主机和端口"
            )
        endpoints.append((label, host, port))
    return endpoints


def _wait_for_endpoint(
    label: str,
    host: str,
    port: int,
    timeout_seconds: float,
) -> None:
    """轮询TCP端口直到可连接；超时抛错，不静默继续。"""

    deadline = time.monotonic() + timeout_seconds
    while True:
        if _can_connect(host, port):
            return
        if time.monotonic() >= deadline:
            raise ServeStartupError(
                f"{label}（{host}:{port}）在 {timeout_seconds:.0f} 秒内未就绪"
            )
        time.sleep(PROBE_INTERVAL_SECONDS)


def _ensure_port_available(host: str, port: int) -> None:
    """确认HTTP端口空闲；被占用时明确报错，避免守护循环反复空转。"""

    if _can_connect(host, port):
        raise ServeStartupError(
            f"{host}:{port} 已被其他进程占用，请先停止占用该端口的服务"
        )


def _can_connect(host: str, port: int) -> bool:
    try:
        with socket.create_connection(
            (host, port),
            timeout=PROBE_TIMEOUT_SECONDS,
        ):
            return True
    except OSError:
        return False


def _serve(api_token: str) -> None:
    """守护运行uvicorn；每轮重建app实例，异常退出后重新拉起。"""

    while True:
        app = create_app(api_token=api_token)
        try:
            uvicorn.run(app, host=API_HOST, port=API_PORT)
        except (Exception, SystemExit) as exc:
            print(f"服务异常退出：{exc!r}")
        print(f"服务进程已退出，{RESTART_DELAY_SECONDS:.0f} 秒后重新拉起")
        time.sleep(RESTART_DELAY_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
