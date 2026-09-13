from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def migrate_staple_components(
    engine: Engine,
    recipe_path: str | Path,
) -> None:
    """在单个PostgreSQL事务中幂等回填主食构成布尔列。"""

    rows = _load_source(Path(recipe_path))
    source_recipes = {row["name"] for row in rows}
    source_relations = {
        (row["name"], ingredient)
        for row in rows
        for ingredient in row["ingredients"]
    }
    staple_relations = {
        (row["name"], ingredient)
        for row in rows
        for ingredient in row["staple_ingredients"]
    }

    with engine.begin() as connection:
        database_recipes = set(
            connection.execute(text("SELECT name FROM recipes")).scalars()
        )
        database_relations = set(
            connection.execute(
                text(
                    "SELECT r.name, i.name "
                    "FROM recipe_ingredients ri "
                    "JOIN recipes r ON r.id = ri.recipe_id "
                    "JOIN ingredients i ON i.id = ri.ingredient_id"
                )
            ).all()
        )
        if database_recipes != source_recipes:
            raise RuntimeError("主食迁移菜谱数或菜谱集不一致")
        if database_relations != source_relations:
            raise RuntimeError("主食迁移食材关联行或关联集不一致")

        columns = {
            column["name"]
            for column in inspect(connection).get_columns("recipe_ingredients")
        }
        if "is_staple_component" not in columns:
            connection.execute(
                text(
                    "ALTER TABLE recipe_ingredients "
                    "ADD COLUMN is_staple_component BOOLEAN NULL"
                )
            )
        reset_result = connection.execute(
            text(
                "UPDATE recipe_ingredients "
                "SET is_staple_component = false"
            )
        )
        if reset_result.rowcount != len(database_relations):
            raise RuntimeError("主食迁移关联行回填不完整")
        for recipe_name, ingredient_name in sorted(staple_relations):
            update_result = connection.execute(
                text(
                    "UPDATE recipe_ingredients ri "
                    "SET is_staple_component = true "
                    "FROM recipes r, ingredients i "
                    "WHERE ri.recipe_id = r.id "
                    "AND ri.ingredient_id = i.id "
                    "AND r.name = :recipe_name "
                    "AND i.name = :ingredient_name"
                ),
                {
                    "recipe_name": recipe_name,
                    "ingredient_name": ingredient_name,
                },
            )
            if update_result.rowcount != 1:
                raise RuntimeError("主食迁移主食关联回填不完整")
        missing_count = connection.execute(
            text(
                "SELECT count(*) FROM recipe_ingredients "
                "WHERE is_staple_component IS NULL"
            )
        ).scalar_one()
        if missing_count:
            raise RuntimeError("主食迁移回填不完整")
        connection.execute(
            text(
                "ALTER TABLE recipe_ingredients "
                "ALTER COLUMN is_staple_component SET NOT NULL"
            )
        )


def _load_source(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"主食迁移源数据读取失败：{exc}") from exc
    if not isinstance(value, list):
        raise RuntimeError("主食迁移源数据必须是数组")
    rows: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise RuntimeError(f"主食迁移源[{index}]必须是对象")
        name = raw.get("name")
        ingredients = raw.get("ingredients")
        staples = raw.get("staple_ingredients")
        if not isinstance(name, str) or not name or name in seen_names:
            raise RuntimeError("主食迁移源菜名非法或重复")
        if not isinstance(ingredients, Mapping):
            raise RuntimeError(f"{name}的ingredients非法")
        if not isinstance(staples, list) or any(
            not isinstance(item, str) or item not in ingredients
            for item in staples
        ):
            raise RuntimeError(f"{name}的staple_ingredients非法")
        if len(staples) != len(set(staples)):
            raise RuntimeError(f"{name}的staple_ingredients重复")
        seen_names.add(name)
        rows.append(dict(raw))
    return rows


__all__ = ["migrate_staple_components"]
