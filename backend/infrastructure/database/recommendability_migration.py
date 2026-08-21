from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine


def migrate_recommendability(engine: Engine, recipes_path: Path) -> None:
    """在单个PostgreSQL事务中为既有菜谱回填推荐资格并收紧约束。

    recipes_path 指向带 is_recommendable 的正式菜谱JSON；
    资格为审计结果、不推导，按菜名逐一回填；数量不一致或回填不完整时
    整批回滚。列已存在时跳过建列，仍按JSON重跑回填（幂等）。
    """

    with recipes_path.open(encoding="utf-8") as stream:
        recipes = json.load(stream)
    if not isinstance(recipes, list):
        raise ValueError("正式菜谱JSON顶层必须是数组")
    by_name: dict[str, bool] = {}
    for recipe in recipes:
        name = recipe.get("name")
        is_recommendable = recipe.get("is_recommendable")
        if not isinstance(name, str) or not name:
            raise ValueError("正式菜谱JSON缺少菜名")
        if type(is_recommendable) is not bool:
            raise ValueError(f"菜谱{name}缺少布尔推荐资格")
        by_name[name] = is_recommendable

    with engine.begin() as connection:
        _add_column_if_missing(connection)
        table_count = connection.scalar(
            text("SELECT COUNT(*) FROM recipes")
        )
        if table_count != len(by_name):
            raise RuntimeError(
                f"菜谱数量不一致：表{table_count}，JSON{len(by_name)}"
            )
        for name, is_recommendable in by_name.items():
            connection.execute(
                text(
                    "UPDATE recipes SET is_recommendable = :value "
                    "WHERE name = :name"
                ),
                {"value": is_recommendable, "name": name},
            )
        invalid_count = connection.scalar(
            text(
                "SELECT COUNT(*) FROM recipes "
                "WHERE is_recommendable IS NULL"
            )
        )
        if invalid_count:
            raise RuntimeError("菜谱推荐资格回填结果不完整")
        connection.execute(
            text(
                "ALTER TABLE recipes "
                "ALTER COLUMN is_recommendable SET NOT NULL"
            )
        )


def _add_column_if_missing(connection: object) -> None:
    exists = connection.scalar(
        text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_name = 'recipes' "
            "AND column_name = 'is_recommendable'"
        )
    )
    if not exists:
        connection.execute(
            text("ALTER TABLE recipes ADD COLUMN is_recommendable BOOLEAN")
        )


__all__ = ["migrate_recommendability"]
