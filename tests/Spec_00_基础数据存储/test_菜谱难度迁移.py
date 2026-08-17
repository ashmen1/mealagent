from __future__ import annotations

import importlib
import json

import pytest
from sqlalchemy import inspect, text


def _load_migration():
    module = importlib.import_module(
        "backend.infrastructure.database.recipe_difficulty_migration"
    )
    return module.migrate_recipe_difficulty


def _create_legacy_schema(db_engine) -> None:
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE recipes ("
                "id BIGINT PRIMARY KEY, "
                "name TEXT NOT NULL UNIQUE, "
                "total_time_lower_bound_minutes INTEGER NOT NULL, "
                "atomic_steps JSONB NOT NULL"
                ")"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE recipe_ingredients ("
                "recipe_id BIGINT NOT NULL REFERENCES recipes(id), "
                "ingredient_id BIGINT NOT NULL, "
                "PRIMARY KEY (recipe_id, ingredient_id)"
                ")"
            )
        )
        rows = [
            {
                "id": 1,
                "name": "边界简单菜",
                "minutes": 20,
                "steps": json.dumps([{}] * 8),
                "ingredient_count": 9,
            },
            {
                "id": 2,
                "name": "边界中等菜",
                "minutes": 60,
                "steps": json.dumps([{}] * 15),
                "ingredient_count": 20,
            },
            {
                "id": 3,
                "name": "超时复杂菜",
                "minutes": 61,
                "steps": json.dumps([{}]),
                "ingredient_count": 1,
            },
        ]
        for row in rows:
            connection.execute(
                text(
                    "INSERT INTO recipes "
                    "(id, name, total_time_lower_bound_minutes, atomic_steps) "
                    "VALUES (:id, :name, :minutes, CAST(:steps AS JSONB))"
                ),
                row,
            )
            connection.execute(
                text(
                    "INSERT INTO recipe_ingredients "
                    "(recipe_id, ingredient_id) "
                    "SELECT :recipe_id, value "
                    "FROM generate_series(1, :ingredient_count) AS value"
                ),
                {
                    "recipe_id": row["id"],
                    "ingredient_count": row["ingredient_count"],
                },
            )


@pytest.fixture
def legacy_db_engine(db_engine):
    from backend.infrastructure.database.models import Base

    Base.metadata.drop_all(db_engine)
    _create_legacy_schema(db_engine)
    yield db_engine


def test_既有数据库在一个事务内回填难度并收紧约束(
    legacy_db_engine,
):
    migrate_recipe_difficulty = _load_migration()

    migrate_recipe_difficulty(legacy_db_engine)

    inspector = inspect(legacy_db_engine)
    difficulty_column = next(
        column
        for column in inspector.get_columns("recipes")
        if column["name"] == "difficulty"
    )
    check_sql = " ".join(
        constraint.get("sqltext", "")
        for constraint in inspector.get_check_constraints("recipes")
    )
    with legacy_db_engine.connect() as connection:
        rows = connection.execute(
            text("SELECT name, difficulty FROM recipes ORDER BY id")
        ).all()

    assert difficulty_column["nullable"] is False
    assert all(value in check_sql for value in ("简单", "中等", "复杂"))
    assert rows == [
        ("边界简单菜", "简单"),
        ("边界中等菜", "中等"),
        ("超时复杂菜", "复杂"),
    ]


def test_难度回填失败时新增列和已有数据一并回滚(
    legacy_db_engine,
):
    migrate_recipe_difficulty = _load_migration()
    with legacy_db_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE OR REPLACE FUNCTION spec_00_fail_difficulty_backfill() "
                "RETURNS trigger AS $$ "
                "BEGIN RAISE EXCEPTION 'forced migration failure'; END; "
                "$$ LANGUAGE plpgsql"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER spec_00_fail_difficulty_backfill_trigger "
                "BEFORE UPDATE ON recipes FOR EACH ROW "
                "EXECUTE FUNCTION spec_00_fail_difficulty_backfill()"
            )
        )

    try:
        with pytest.raises(Exception, match="forced migration failure"):
            migrate_recipe_difficulty(legacy_db_engine)

        assert "difficulty" not in {
            column["name"]
            for column in inspect(legacy_db_engine).get_columns("recipes")
        }
        with legacy_db_engine.connect() as connection:
            names = connection.scalars(
                text("SELECT name FROM recipes ORDER BY id")
            ).all()
        assert names == ["边界简单菜", "边界中等菜", "超时复杂菜"]
    finally:
        with legacy_db_engine.begin() as connection:
            connection.execute(
                text(
                    "DROP FUNCTION IF EXISTS "
                    "spec_00_fail_difficulty_backfill() CASCADE"
                )
            )
