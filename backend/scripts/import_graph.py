# Set-Location D:\codes\A_mealagent_v3
# uv run python -m backend.scripts.import_graph

import tomllib
from pathlib import Path

from backend.infrastructure.database import (
    create_database_engine,
    create_session_factory,
)
from backend.infrastructure.graph import (
    GraphImportError,
    import_graph_data,
)

root = Path.cwd()

with (root / "pyproject.toml").open("rb") as stream:
    project_config = tomllib.load(stream)
database_url = project_config["tool"]["mealagent"]["database"]["url"]
neo4j_config = project_config["tool"]["mealagent"]["neo4j"]

engine = create_database_engine(database_url)
try:
    session_factory = create_session_factory(engine)
    result = import_graph_data(
        session_factory,
        neo4j_config["uri"],
        neo4j_config["user"],
        neo4j_config["password"],
    )
    print(result)
except GraphImportError as exc:
    print(f"导入失败：status_code={exc.status_code}, message={exc}")
finally:
    engine.dispose()
