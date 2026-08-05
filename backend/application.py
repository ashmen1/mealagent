from __future__ import annotations

import tomllib
from pathlib import Path

from sqlalchemy.engine import Engine

from backend.infrastructure.database import (
    create_database_engine,
    create_session_factory,
)
from backend.infrastructure.llm import (
    create_langchain_constraint_extractor_from_environment,
)
from backend.services import DialogueConstraintService, ProfileConstraintService


PYPROJECT_PATH = Path(__file__).resolve().parents[1] / "pyproject.toml"


class ApplicationConfigurationError(ValueError):
    """应用运行配置不完整或不合法。"""

    status_code = 500


class ConstraintServices:
    """统一持有约束提取Service及其共享基础设施。"""

    def __init__(
        self,
        engine: Engine,
        profile: ProfileConstraintService,
        dialogue: DialogueConstraintService,
    ) -> None:
        self._engine = engine
        self.profile = profile
        self.dialogue = dialogue
        self._is_closed = False

    def __enter__(self) -> ConstraintServices:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        """释放应用容器持有的数据库Engine。"""

        if self._is_closed:
            return
        self._engine.dispose()
        self._is_closed = True


def create_constraint_services() -> ConstraintServices:
    """从项目配置创建可长期复用的约束提取Service。"""

    database_url = _load_database_url(PYPROJECT_PATH)
    engine = create_database_engine(database_url)
    try:
        session_factory = create_session_factory(engine)
        llm_client = create_langchain_constraint_extractor_from_environment()
        return ConstraintServices(
            engine=engine,
            profile=ProfileConstraintService(session_factory),
            dialogue=DialogueConstraintService(session_factory, llm_client),
        )
    except BaseException:
        engine.dispose()
        raise


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


__all__ = [
    "ApplicationConfigurationError",
    "ConstraintServices",
    "create_constraint_services",
]
