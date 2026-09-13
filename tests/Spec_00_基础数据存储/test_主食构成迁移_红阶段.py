from __future__ import annotations

import importlib
import json

import pytest
from sqlalchemy import inspect, text


def _load_migration():
    module = importlib.import_module(
        "backend.infrastructure.database.staple_component_migration"
    )
    return module.migrate_staple_components


def _write_recipes_json(tmp_path, rows):
    path = tmp_path / "recipes.json"
    path.write_text(
        json.dumps(rows, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _create_legacy_schema(db_engine) -> None:
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE recipes ("
                "id BIGINT PRIMARY KEY, name TEXT NOT NULL UNIQUE)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE ingredients ("
                "id BIGINT PRIMARY KEY, name TEXT NOT NULL UNIQUE)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE recipe_ingredients ("
                "recipe_id BIGINT NOT NULL, ingredient_id BIGINT NOT NULL, "
                "PRIMARY KEY (recipe_id, ingredient_id))"
            )
        )
        connection.execute(
            text(
                "INSERT INTO recipes (id, name) VALUES "
                "(1, '培根披萨'), (2, '蜜汁烤玉米'), (3, '红薯米饭')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO ingredients (id, name) VALUES "
                "(1, '高筋面粉'), (2, '玉米'), (3, '大米'), (4, '红薯')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO recipe_ingredients (recipe_id, ingredient_id) VALUES "
                "(1, 1), (1, 2), (2, 2), (3, 3), (3, 4)"
            )
        )


@pytest.fixture
def legacy_staple_engine(db_engine):
    from backend.infrastructure.database.models import Base

    Base.metadata.drop_all(db_engine)
    _create_legacy_schema(db_engine)
    yield db_engine


def _valid_source():
    return [
        {
            "name": "培根披萨",
            "ingredients": {"高筋面粉": "200g", "玉米": "30g"},
            "staple_ingredients": ["高筋面粉"],
        },
        {
            "name": "蜜汁烤玉米",
            "ingredients": {"玉米": "200g"},
            "staple_ingredients": ["玉米"],
        },
        {
            "name": "红薯米饭",
            "ingredients": {"大米": "100g", "红薯": "100g"},
            "staple_ingredients": ["大米", "红薯"],
        },
    ]


def test_迁移完整回填并收紧为非空布尔列(
    legacy_staple_engine,
    tmp_path,
) -> None:
    migrate = _load_migration()
    source = _write_recipes_json(tmp_path, _valid_source())

    migrate(legacy_staple_engine, source)

    column = next(
        item
        for item in inspect(legacy_staple_engine).get_columns(
            "recipe_ingredients"
        )
        if item["name"] == "is_staple_component"
    )
    assert column["nullable"] is False
    with legacy_staple_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT r.name, i.name, ri.is_staple_component "
                "FROM recipe_ingredients ri "
                "JOIN recipes r ON r.id=ri.recipe_id "
                "JOIN ingredients i ON i.id=ri.ingredient_id "
                "ORDER BY r.id, i.id"
            )
        ).all()
    assert rows == [
        ("培根披萨", "高筋面粉", True),
        ("培根披萨", "玉米", False),
        ("蜜汁烤玉米", "玉米", True),
        ("红薯米饭", "大米", True),
        ("红薯米饭", "红薯", True),
    ]


def test_迁移数量不一致时新增列与回填一并回滚(
    legacy_staple_engine,
    tmp_path,
) -> None:
    migrate = _load_migration()
    source = _write_recipes_json(tmp_path, _valid_source()[:-1])

    with pytest.raises(RuntimeError, match="菜谱数|关联行数"):
        migrate(legacy_staple_engine, source)

    assert "is_staple_component" not in {
        column["name"]
        for column in inspect(legacy_staple_engine).get_columns(
            "recipe_ingredients"
        )
    }


def test_迁移食材关联集不一致时整体回滚(
    legacy_staple_engine,
    tmp_path,
) -> None:
    migrate = _load_migration()
    rows = _valid_source()
    rows[0]["ingredients"] = {"高筋面粉": "200g"}
    source = _write_recipes_json(tmp_path, rows)

    with pytest.raises(RuntimeError, match="关联|食材"):
        migrate(legacy_staple_engine, source)

    assert "is_staple_component" not in {
        column["name"]
        for column in inspect(legacy_staple_engine).get_columns(
            "recipe_ingredients"
        )
    }


def test_迁移中途失败不留下部分布尔值(
    legacy_staple_engine,
    tmp_path,
) -> None:
    migrate = _load_migration()
    source = _write_recipes_json(tmp_path, _valid_source())
    with legacy_staple_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE OR REPLACE FUNCTION spec_00_fail_staple_backfill() "
                "RETURNS trigger AS $$ BEGIN "
                "RAISE EXCEPTION 'forced staple migration failure'; "
                "END; $$ LANGUAGE plpgsql"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER spec_00_fail_staple_backfill_trigger "
                "BEFORE UPDATE ON recipe_ingredients FOR EACH ROW "
                "EXECUTE FUNCTION spec_00_fail_staple_backfill()"
            )
        )

    try:
        with pytest.raises(Exception, match="forced staple migration failure"):
            migrate(legacy_staple_engine, source)
        assert "is_staple_component" not in {
            column["name"]
            for column in inspect(legacy_staple_engine).get_columns(
                "recipe_ingredients"
            )
        }
    finally:
        with legacy_staple_engine.begin() as connection:
            connection.execute(
                text(
                    "DROP FUNCTION IF EXISTS "
                    "spec_00_fail_staple_backfill() CASCADE"
                )
            )


def test_迁移重复执行保持相同结果(
    legacy_staple_engine,
    tmp_path,
) -> None:
    migrate = _load_migration()
    source = _write_recipes_json(tmp_path, _valid_source())

    migrate(legacy_staple_engine, source)
    migrate(legacy_staple_engine, source)

    with legacy_staple_engine.connect() as connection:
        values = connection.scalars(
            text(
                "SELECT is_staple_component FROM recipe_ingredients "
                "ORDER BY recipe_id, ingredient_id"
            )
        ).all()
    assert values == [True, False, True, True, True]
