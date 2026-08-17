from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def migrate_recipe_difficulty(engine: Engine) -> None:
    """在单个 PostgreSQL 事务中为既有菜谱回填并约束难度。"""

    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE recipes ADD COLUMN difficulty VARCHAR")
        )
        connection.execute(
            text(
                "UPDATE recipes AS recipe SET difficulty = CASE "
                "WHEN recipe.total_time_lower_bound_minutes <= 20 "
                "AND json_array_length(recipe.atomic_steps::json) <= 8 "
                "AND (SELECT COUNT(DISTINCT relation.ingredient_id) "
                "FROM recipe_ingredients AS relation "
                "WHERE relation.recipe_id = recipe.id) <= 9 THEN '简单' "
                "WHEN recipe.total_time_lower_bound_minutes > 60 "
                "OR json_array_length(recipe.atomic_steps::json) > 15 "
                "OR (SELECT COUNT(DISTINCT relation.ingredient_id) "
                "FROM recipe_ingredients AS relation "
                "WHERE relation.recipe_id = recipe.id) > 20 THEN '复杂' "
                "ELSE '中等' END"
            )
        )
        invalid_count = connection.scalar(
            text(
                "SELECT COUNT(*) FROM recipes "
                "WHERE difficulty IS NULL "
                "OR difficulty NOT IN ('简单', '中等', '复杂')"
            )
        )
        if invalid_count:
            raise RuntimeError("菜谱难度回填结果不完整")
        connection.execute(
            text("ALTER TABLE recipes ALTER COLUMN difficulty SET NOT NULL")
        )
        connection.execute(
            text(
                "ALTER TABLE recipes ADD CONSTRAINT ck_recipes_difficulty "
                "CHECK (difficulty IN ('简单', '中等', '复杂'))"
            )
        )


__all__ = ["migrate_recipe_difficulty"]
