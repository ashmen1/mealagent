"""端到端测试共享的图数据保障：确保 Neo4j 图与 PostgreSQL 菜谱一致。

Spec_04 端到端集成与 Spec_06 50x14 都依赖全量真实图数据，
而 Spec_04 的 Neo4j 集成测试会清空图并塞入小图，本模块负责恢复。
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parent.parent
repo_root_text = str(REPO_ROOT)
if repo_root_text not in sys.path:
    sys.path.insert(0, repo_root_text)

from backend.infrastructure.database import (
    create_database_engine,
    create_session_factory,
)
from backend.infrastructure.graph import import_graph_data
from backend.infrastructure.graph.neo4j import create_neo4j_driver


def ensure_graph_data() -> None:
    """确保 Neo4j 图菜谱数与 PostgreSQL 一致；不一致时清空后重导。"""

    with (REPO_ROOT / "pyproject.toml").open("rb") as stream:
        project_config = tomllib.load(stream)
    neo4j_config = project_config["tool"]["mealagent"]["neo4j"]
    uri = neo4j_config["uri"]
    user = neo4j_config["user"]
    password = neo4j_config["password"]
    database_url = project_config["tool"]["mealagent"]["database"]["url"]

    engine = create_database_engine(database_url)
    try:
        session_factory = create_session_factory(engine)
        with engine.connect() as connection:
            postgres_recipe_count = connection.execute(
                text("SELECT count(*) FROM recipes")
            ).scalar()
        driver = create_neo4j_driver(uri, user, password)
        try:
            with driver.session() as session:
                graph_count = session.run(
                    "MATCH (r:Recipe) RETURN count(r) AS c"
                ).single()["c"]
            if graph_count == postgres_recipe_count:
                return
            # 图被其他集成测试清空或混入测试菜谱，先清空再重导
            with driver.session() as session:
                session.run("MATCH (n) DETACH DELETE n")
        finally:
            driver.close()
        import_graph_data(session_factory, uri, user, password)
    finally:
        engine.dispose()


__all__ = ["ensure_graph_data"]
