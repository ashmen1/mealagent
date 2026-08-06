# Set-Location D:\codes\A_mealagent_v3
# uv run python -m backend.scripts.reset_and_import

"""清空业务数据库与图数据库，导入真实数据。"""

import tomllib
from pathlib import Path

from sqlalchemy import text

from backend.infrastructure.database import (
    create_database_engine,
    create_session_factory,
    import_basic_data,
)
from backend.infrastructure.database.models import Base
from backend.infrastructure.graph import (
    GraphImportError,
    import_graph_data,
)
from backend.infrastructure.graph.neo4j import create_neo4j_driver

root = Path.cwd()
with (root / "pyproject.toml").open("rb") as stream:
    project_config = tomllib.load(stream)
database_url = project_config["tool"]["mealagent"]["database"]["url"]
neo4j_config = project_config["tool"]["mealagent"]["neo4j"]

engine = create_database_engine(database_url)
neo4j_driver = create_neo4j_driver(
    neo4j_config["uri"],
    neo4j_config["user"],
    neo4j_config["password"],
)
try:
    with engine.begin() as connection:
        Base.metadata.drop_all(connection)
        Base.metadata.create_all(connection)
    print("PostgreSQL 已清空并重建表结构")

    with neo4j_driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")
    print("Neo4j 已清空")

    session_factory = create_session_factory(engine)
    with session_factory() as session:
        result = import_basic_data(
            root / "datas/processed/Recipes/RecipeComplete.json",
            root / "datas/processed/Ingredients/Ingredients2Nutrition.csv",
            root / "datas/processed/users/50个用户健康档案_归一化.json",
            session,
        )
        print("PG 导入结果:", result)

    try:
        graph_result = import_graph_data(
            session_factory,
            neo4j_config["uri"],
            neo4j_config["user"],
            neo4j_config["password"],
        )
        print("Neo4j 导入结果:", graph_result)
    except GraphImportError as exc:
        print(f"Neo4j 导入失败：status_code={exc.status_code}, message={exc}")
finally:
    neo4j_driver.close()
    engine.dispose()
