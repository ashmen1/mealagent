from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from backend.core.recipe_pairing import (
    COMPOSITION_TYPES,
    PAIRING_ATTRIBUTE_FIELDS,
    PRIMARY_COOKING_METHODS,
    SERVING_TEMPERATURES,
)


EXPECTED_RECIPE_COUNT = 1912


class RecipePairingSyncError(Exception):
    """菜谱搭配属性同步或一致性校验错误。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def sync_recipe_pairing_attributes(
    engine: Engine,
    neo4j_driver: Any,
    recipe_path: str | Path,
    *,
    expected_recipe_count: int = EXPECTED_RECIPE_COUNT,
) -> dict[str, int]:
    """按正式 JSON 原位同步两库，并在写入后执行三方核验。"""

    expected = _load_recipe_snapshot(
        Path(recipe_path),
        expected_recipe_count,
    )
    _sync_postgresql(engine, expected)
    _sync_neo4j(neo4j_driver, expected)
    return _validate_storage_snapshots(expected, engine, neo4j_driver)


def validate_recipe_pairing_consistency(
    engine: Engine,
    neo4j_driver: Any,
    recipe_path: str | Path,
    *,
    expected_recipe_count: int = EXPECTED_RECIPE_COUNT,
) -> dict[str, int]:
    """只读核验正式 JSON、PostgreSQL 和 Neo4j 的搭配属性。"""

    expected = _load_recipe_snapshot(
        Path(recipe_path),
        expected_recipe_count,
    )
    return _validate_storage_snapshots(expected, engine, neo4j_driver)


def _validate_storage_snapshots(
    expected: dict[str, dict[str, str]],
    engine: Engine,
    neo4j_driver: Any,
) -> dict[str, int]:
    """读取并核对两套存储，统一生成一致性计数。"""

    postgresql = _load_postgresql_snapshot(engine)
    neo4j = _load_neo4j_snapshot(neo4j_driver)
    _require_identical(expected, postgresql, "PostgreSQL")
    _require_identical(expected, neo4j, "Neo4j")
    return {
        "recipe_count": len(expected),
        "postgresql_count": len(postgresql),
        "neo4j_count": len(neo4j),
    }


def _load_recipe_snapshot(
    recipe_path: Path,
    expected_recipe_count: int,
) -> dict[str, dict[str, str]]:
    if type(expected_recipe_count) is not int or expected_recipe_count <= 0:
        raise RecipePairingSyncError(400, "expected_recipe_count必须是正整数")
    try:
        raw = json.loads(recipe_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RecipePairingSyncError(
            400,
            f"无法读取正式菜谱JSON：{exc}",
        ) from exc
    if not isinstance(raw, list):
        raise RecipePairingSyncError(400, "正式菜谱JSON顶层必须是数组")
    if len(raw) != expected_recipe_count:
        raise RecipePairingSyncError(
            400,
            f"正式菜谱数量必须为{expected_recipe_count}，实际为{len(raw)}",
        )

    result: dict[str, dict[str, str]] = {}
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            raise RecipePairingSyncError(400, f"recipes[{index}]必须是对象")
        name = value.get("name")
        if not isinstance(name, str) or not name.strip():
            raise RecipePairingSyncError(
                400,
                f"recipes[{index}].name必须是非空字符串",
            )
        if name in result:
            raise RecipePairingSyncError(400, f"菜谱名称重复：{name}")
        attributes = {
            field: value.get(field) for field in PAIRING_ATTRIBUTE_FIELDS
        }
        _validate_attributes(name, attributes, 400)
        result[name] = attributes
    return result


def _validate_attributes(
    recipe_name: str,
    attributes: dict[str, Any],
    status_code: int,
) -> None:
    allowed = {
        "composition_type": COMPOSITION_TYPES,
        "serving_temperature": SERVING_TEMPERATURES,
        "primary_cooking_method": PRIMARY_COOKING_METHODS,
    }
    for field, values in allowed.items():
        if attributes.get(field) not in values:
            raise RecipePairingSyncError(
                status_code,
                f"菜谱{recipe_name}的{field}缺失或非法",
            )


def _sync_postgresql(
    engine: Engine,
    expected: dict[str, dict[str, str]],
) -> None:
    try:
        with engine.begin() as connection:
            for field in PAIRING_ATTRIBUTE_FIELDS:
                exists = connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM information_schema.columns "
                        "WHERE table_name = 'recipes' AND column_name = :field"
                    ),
                    {"field": field},
                )
                if not exists:
                    connection.execute(
                        text(f"ALTER TABLE recipes ADD COLUMN {field} VARCHAR")
                    )

            database_names = set(
                connection.scalars(text("SELECT name FROM recipes")).all()
            )
            if database_names != set(expected):
                raise RecipePairingSyncError(
                    409,
                    "PostgreSQL菜谱名称集合与正式JSON不一致",
                )
            connection.execute(
                text(
                    "UPDATE recipes SET "
                    "composition_type = :composition_type, "
                    "serving_temperature = :serving_temperature, "
                    "primary_cooking_method = :primary_cooking_method "
                    "WHERE name = :name"
                ),
                _build_sync_rows(expected),
            )
            for field in PAIRING_ATTRIBUTE_FIELDS:
                connection.execute(
                    text(
                        f"ALTER TABLE recipes ALTER COLUMN {field} SET NOT NULL"
                    )
                )
            _add_postgresql_constraints(connection)
    except RecipePairingSyncError:
        raise
    except Exception as exc:
        raise RecipePairingSyncError(
            500,
            f"PostgreSQL搭配属性同步失败：{exc}",
        ) from exc


def _add_postgresql_constraints(connection: Any) -> None:
    constraints = {
        "ck_recipes_composition_type": (
            "composition_type IN ('荤', '素')"
        ),
        "ck_recipes_serving_temperature": (
            "serving_temperature IN ('热', '冷')"
        ),
        "ck_recipes_primary_cooking_method": (
            "primary_cooking_method IN "
            "('蒸', '煮', '炒', '炖', '煎', '炸', '烤', '拌', "
            "'烧焖', '冷制', '其他')"
        ),
    }
    for name, expression in constraints.items():
        exists = connection.scalar(
            text(
                "SELECT COUNT(*) FROM pg_constraint WHERE conname = :name"
            ),
            {"name": name},
        )
        if not exists:
            connection.execute(
                text(
                    f"ALTER TABLE recipes ADD CONSTRAINT {name} "
                    f"CHECK ({expression})"
                )
            )


def _sync_neo4j(
    neo4j_driver: Any,
    expected: dict[str, dict[str, str]],
) -> None:
    rows = _build_sync_rows(expected)
    try:
        with neo4j_driver.session() as session:
            def write(transaction: Any) -> None:
                existing = {
                    record["name"]
                    for record in transaction.run(
                        "MATCH (r:Recipe) RETURN r.name AS name"
                    )
                }
                if existing != set(expected):
                    raise RecipePairingSyncError(
                        409,
                        "Neo4j菜谱名称集合与正式JSON不一致",
                    )
                transaction.run(
                    "UNWIND $rows AS row "
                    "MATCH (r:Recipe {name: row.name}) "
                    "SET r.composition_type = row.composition_type, "
                    "r.serving_temperature = row.serving_temperature, "
                    "r.primary_cooking_method = row.primary_cooking_method",
                    rows=rows,
                ).consume()

            session.execute_write(write)
    except RecipePairingSyncError:
        raise
    except Exception as exc:
        raise RecipePairingSyncError(
            500,
            f"Neo4j搭配属性同步失败：{exc}",
        ) from exc


def _load_postgresql_snapshot(
    engine: Engine,
) -> dict[str, dict[str, str]]:
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT name, composition_type, serving_temperature, "
                    "primary_cooking_method FROM recipes ORDER BY name"
                )
            ).mappings()
            return _build_snapshot(rows)
    except Exception as exc:
        raise RecipePairingSyncError(
            500,
            f"读取PostgreSQL搭配属性失败：{exc}",
        ) from exc


def _load_neo4j_snapshot(
    neo4j_driver: Any,
) -> dict[str, dict[str, str]]:
    try:
        with neo4j_driver.session() as session:
            records = session.run(
                "MATCH (r:Recipe) "
                "RETURN r.name AS name, "
                "r.composition_type AS composition_type, "
                "r.serving_temperature AS serving_temperature, "
                "r.primary_cooking_method AS primary_cooking_method "
                "ORDER BY r.name"
            )
            return _build_snapshot(records)
    except Exception as exc:
        raise RecipePairingSyncError(
            500,
            f"读取Neo4j搭配属性失败：{exc}",
        ) from exc


def _build_sync_rows(
    expected: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    return [
        {"name": name, **attributes}
        for name, attributes in expected.items()
    ]


def _build_snapshot(records: Any) -> dict[str, dict[str, str]]:
    return {
        record["name"]: {
            field: record[field] for field in PAIRING_ATTRIBUTE_FIELDS
        }
        for record in records
    }


def _require_identical(
    expected: dict[str, dict[str, str]],
    actual: dict[str, dict[str, str]],
    source_name: str,
) -> None:
    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise RecipePairingSyncError(
            500,
            f"{source_name}菜谱集合不一致：缺少{missing[:3]}，额外{extra[:3]}",
        )
    for name, attributes in expected.items():
        actual_attributes = actual[name]
        for field in PAIRING_ATTRIBUTE_FIELDS:
            if actual_attributes.get(field) != attributes[field]:
                raise RecipePairingSyncError(
                    500,
                    f"{source_name}菜谱{name}的{field}不一致："
                    f"期望{attributes[field]}，实际{actual_attributes.get(field)}",
                )


__all__ = [
    "EXPECTED_RECIPE_COUNT",
    "RecipePairingSyncError",
    "sync_recipe_pairing_attributes",
    "validate_recipe_pairing_consistency",
]
