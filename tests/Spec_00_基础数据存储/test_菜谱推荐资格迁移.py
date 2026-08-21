from __future__ import annotations

import importlib
import json

import pytest
from sqlalchemy import inspect, text


def _load_migration():
    module = importlib.import_module(
        "backend.infrastructure.database.recommendability_migration"
    )
    return module.migrate_recommendability


def _write_recipes_json(tmp_path, rows):
    path = tmp_path / "recipes.json"
    path.write_text(
        json.dumps(rows, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _create_legacy_schema(db_engine) -> None:
    """构造无 is_recommendable 列的旧版菜谱表。"""

    with db_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE recipes ("
                "id BIGINT PRIMARY KEY, "
                "name TEXT NOT NULL UNIQUE"
                ")"
            )
        )
        for row in (
            {"id": 1, "name": "番茄炒蛋"},
            {"id": 2, "name": "清蒸鲈鱼"},
            {"id": 3, "name": "葱段"},
        ):
            connection.execute(
                text("INSERT INTO recipes (id, name) VALUES (:id, :name)"),
                row,
            )


@pytest.fixture
def legacy_db_engine(db_engine):
    from backend.infrastructure.database.models import Base

    Base.metadata.drop_all(db_engine)
    _create_legacy_schema(db_engine)
    yield db_engine


def test_既有数据库回填推荐资格并收紧约束(
    legacy_db_engine,
    tmp_path,
):
    migrate_recommendability = _load_migration()
    recipes_path = _write_recipes_json(
        tmp_path,
        [
            {"name": "番茄炒蛋", "is_recommendable": True},
            {"name": "清蒸鲈鱼", "is_recommendable": True},
            {"name": "葱段", "is_recommendable": False},
        ],
    )

    migrate_recommendability(legacy_db_engine, recipes_path)

    column = next(
        item
        for item in inspect(legacy_db_engine).get_columns("recipes")
        if item["name"] == "is_recommendable"
    )
    assert column["nullable"] is False
    with legacy_db_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT name, is_recommendable FROM recipes ORDER BY id"
            )
        ).all()
    assert rows == [
        ("番茄炒蛋", True),
        ("清蒸鲈鱼", True),
        ("葱段", False),
    ]


def test_回填失败时新增列和已有数据一并回滚(
    legacy_db_engine,
    tmp_path,
):
    migrate_recommendability = _load_migration()
    recipes_path = _write_recipes_json(
        tmp_path,
        [
            {"name": "番茄炒蛋", "is_recommendable": True},
            {"name": "清蒸鲈鱼", "is_recommendable": True},
            {"name": "葱段", "is_recommendable": False},
        ],
    )
    with legacy_db_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE OR REPLACE FUNCTION spec_00_fail_backfill() "
                "RETURNS trigger AS $$ "
                "BEGIN RAISE EXCEPTION 'forced migration failure'; END; "
                "$$ LANGUAGE plpgsql"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER spec_00_fail_backfill_trigger "
                "BEFORE UPDATE ON recipes FOR EACH ROW "
                "EXECUTE FUNCTION spec_00_fail_backfill()"
            )
        )

    try:
        with pytest.raises(Exception, match="forced migration failure"):
            migrate_recommendability(legacy_db_engine, recipes_path)

        assert "is_recommendable" not in {
            column["name"]
            for column in inspect(legacy_db_engine).get_columns("recipes")
        }
        with legacy_db_engine.connect() as connection:
            names = connection.scalars(
                text("SELECT name FROM recipes ORDER BY id")
            ).all()
        assert names == ["番茄炒蛋", "清蒸鲈鱼", "葱段"]
    finally:
        with legacy_db_engine.begin() as connection:
            connection.execute(
                text(
                    "DROP FUNCTION IF EXISTS "
                    "spec_00_fail_backfill() CASCADE"
                )
            )


def test_菜谱数量不一致时整批回滚(
    legacy_db_engine,
    tmp_path,
):
    migrate_recommendability = _load_migration()
    recipes_path = _write_recipes_json(
        tmp_path,
        [
            {"name": "番茄炒蛋", "is_recommendable": True},
            {"name": "清蒸鲈鱼", "is_recommendable": True},
        ],
    )

    with pytest.raises(RuntimeError, match="菜谱数量不一致"):
        migrate_recommendability(legacy_db_engine, recipes_path)

    assert "is_recommendable" not in {
        column["name"]
        for column in inspect(legacy_db_engine).get_columns("recipes")
    }


def test_重跑迁移幂等(
    legacy_db_engine,
    tmp_path,
):
    migrate_recommendability = _load_migration()
    recipes_path = _write_recipes_json(
        tmp_path,
        [
            {"name": "番茄炒蛋", "is_recommendable": True},
            {"name": "清蒸鲈鱼", "is_recommendable": False},
            {"name": "葱段", "is_recommendable": False},
        ],
    )

    migrate_recommendability(legacy_db_engine, recipes_path)
    migrate_recommendability(legacy_db_engine, recipes_path)

    with legacy_db_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT name, is_recommendable FROM recipes ORDER BY id"
            )
        ).all()
    assert rows == [
        ("番茄炒蛋", True),
        ("清蒸鲈鱼", False),
        ("葱段", False),
    ]
