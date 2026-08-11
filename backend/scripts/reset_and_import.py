# Set-Location D:\codes\A_mealagent_v3
# uv run python -m backend.scripts.reset_and_import

"""清空业务数据库与图数据库，导入真实数据。"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Final

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.infrastructure.database import (
    create_database_engine,
    create_session_factory,
    import_basic_data,
)
from backend.infrastructure.database.models import Base
from backend.infrastructure.graph import GraphImportError, import_graph_data
from backend.infrastructure.graph.neo4j import create_neo4j_driver


REPOSITORY_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
PROJECT_CONFIG_PATH: Final[Path] = REPOSITORY_ROOT / "pyproject.toml"
RECIPE_PATH: Final[Path] = (
    REPOSITORY_ROOT / "datas/processed/Recipes/RecipeComplete.json"
)
INGREDIENT_PATH: Final[Path] = (
    REPOSITORY_ROOT
    / "datas/processed/Ingredients/Ingredients2Nutrition.csv"
)
PROFILE_PATH: Final[Path] = (
    REPOSITORY_ROOT
    / "datas/processed/users/50个用户健康档案_归一化.json"
)
DRI_PATH: Final[Path] = (
    REPOSITORY_ROOT / "datas/processed/Nutrition/DRI2023.csv"
)


def show_graph_import_progress(stage: str, completed: int, total: int) -> None:
    """将 Neo4j 各导入阶段的完成量输出到控制台。"""
    percentage = 100.0 if total == 0 else completed / total * 100
    print(
        f"Neo4j 导入进度 [{stage}] "
        f"{completed}/{total} ({percentage:.1f}%)",
        flush=True,
    )


def load_project_config() -> tuple[str, dict[str, Any]]:
    """读取数据库配置，并返回 PostgreSQL URL 与 Neo4j 配置。"""
    with PROJECT_CONFIG_PATH.open("rb") as stream:
        project_config = tomllib.load(stream)
    return (
        project_config["tool"]["mealagent"]["database"]["url"],
        project_config["tool"]["mealagent"]["neo4j"],
    )


def reset_postgresql(engine: Engine) -> None:
    """重建 PostgreSQL 业务表。"""
    with engine.begin() as connection:
        Base.metadata.drop_all(connection)
        Base.metadata.create_all(connection)
    print("PostgreSQL 已清空并重建表结构")


def reset_neo4j(driver: Any) -> None:
    """删除 Neo4j 中的全部节点与关系。"""
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")
    print("Neo4j 已清空")


def import_postgresql(
    session_factory: sessionmaker[Session],
) -> dict[str, dict[str, int]]:
    """导入 PostgreSQL 基础数据与营养派生数据。"""
    with session_factory() as session:
        return import_basic_data(
            RECIPE_PATH,
            INGREDIENT_PATH,
            PROFILE_PATH,
            DRI_PATH,
            session,
        )


def import_neo4j(
    session_factory: sessionmaker[Session],
    neo4j_config: dict[str, Any],
) -> dict[str, Any]:
    """将 PostgreSQL 中的基础数据同步到 Neo4j。"""
    print("开始导入 Neo4j 图数据...", flush=True)
    return import_graph_data(
        session_factory,
        neo4j_config["uri"],
        neo4j_config["user"],
        neo4j_config["password"],
        progress_callback=show_graph_import_progress,
    )


def main() -> int:
    """执行清库、PostgreSQL 导入和 Neo4j 同步。"""
    database_url, neo4j_config = load_project_config()
    engine = create_database_engine(database_url)
    neo4j_driver = create_neo4j_driver(
        neo4j_config["uri"],
        neo4j_config["user"],
        neo4j_config["password"],
    )
    try:
        reset_postgresql(engine)
        reset_neo4j(neo4j_driver)

        session_factory = create_session_factory(engine)
        postgres_result = import_postgresql(session_factory)
        print("PG 导入结果:", postgres_result)

        try:
            graph_result = import_neo4j(session_factory, neo4j_config)
            print("Neo4j 导入结果:", graph_result)
        except GraphImportError as exc:
            print(
                "Neo4j 导入失败："
                f"status_code={exc.status_code}, message={exc}"
            )
        return 0
    finally:
        neo4j_driver.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
