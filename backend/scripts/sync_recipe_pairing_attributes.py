from __future__ import annotations

from backend.application import PYPROJECT_PATH, RECIPE_PATH, _load_neo4j_config
from backend.infrastructure.database import create_database_engine
from backend.infrastructure.graph.neo4j import create_neo4j_driver
from backend.infrastructure.recipe_pairing_sync import (
    sync_recipe_pairing_attributes,
)
from backend.application import _load_database_url


def main() -> int:
    """将正式 JSON 的菜谱搭配属性原位同步到两套数据库。"""

    database_url = _load_database_url(PYPROJECT_PATH)
    neo4j_config = _load_neo4j_config(PYPROJECT_PATH)
    engine = create_database_engine(database_url)
    driver = create_neo4j_driver(
        neo4j_config["uri"],
        neo4j_config["user"],
        neo4j_config["password"],
    )
    try:
        result = sync_recipe_pairing_attributes(
            engine,
            driver,
            RECIPE_PATH,
        )
        print(result)
        return 0
    finally:
        driver.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
