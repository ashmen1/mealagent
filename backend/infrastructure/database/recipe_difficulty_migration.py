from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from backend.core.recipe_difficulty import (
    COMPLEX_ATOMIC_STEPS_THRESHOLD,
    COMPLEX_INGREDIENT_COUNT_THRESHOLD,
    COMPLEX_TOTAL_TIME_MINUTES_THRESHOLD,
    SIMPLE_MAX_ATOMIC_STEPS,
    SIMPLE_MAX_INGREDIENT_COUNT,
    SIMPLE_MAX_TOTAL_TIME_MINUTES,
)


def migrate_recipe_difficulty(engine: Engine) -> None:
    """在单个 PostgreSQL 事务中为既有菜谱回填并约束难度。"""

    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE recipes ADD COLUMN difficulty VARCHAR")
        )
        connection.execute(
            text(
                f"UPDATE recipes AS recipe SET difficulty = CASE "
                f"WHEN recipe.total_time_lower_bound_minutes "
                f"<= {SIMPLE_MAX_TOTAL_TIME_MINUTES} "
                f"AND json_array_length(recipe.atomic_steps::json) "
                f"<= {SIMPLE_MAX_ATOMIC_STEPS} "
                "AND (SELECT COUNT(DISTINCT relation.ingredient_id) "
                "FROM recipe_ingredients AS relation "
                f"WHERE relation.recipe_id = recipe.id) "
                f"<= {SIMPLE_MAX_INGREDIENT_COUNT} THEN '简单' "
                f"WHEN recipe.total_time_lower_bound_minutes "
                f"> {COMPLEX_TOTAL_TIME_MINUTES_THRESHOLD} "
                f"OR json_array_length(recipe.atomic_steps::json) "
                f"> {COMPLEX_ATOMIC_STEPS_THRESHOLD} "
                "OR (SELECT COUNT(DISTINCT relation.ingredient_id) "
                "FROM recipe_ingredients AS relation "
                f"WHERE relation.recipe_id = recipe.id) "
                f"> {COMPLEX_INGREDIENT_COUNT_THRESHOLD} THEN '复杂' "
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
