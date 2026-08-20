from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.core.dish_filtering_contract import (
    ALLERGEN_CONCEPT_MEMBERS,
    AUXILIARY_INGREDIENTS,
    CONCEPT_KINDS,
    GROUP_TAGS,
)
from backend.infrastructure.database.models import (
    Ingredient,
    Recipe,
    RecipeIngredient,
)
from backend.infrastructure.graph.neo4j import create_neo4j_driver


class GraphImportError(Exception):
    """图数据导入错误。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


GraphImportProgressCallback = Callable[[str, int, int], None]
_PROGRESS_INTERVAL = 250
_Item = TypeVar("_Item")


@dataclass(frozen=True)
class _GraphSourceData:
    recipes: list[Recipe]
    ingredients: list[Ingredient]
    recipe_ingredients: list[RecipeIngredient]
    ingredient_names: dict[int, str]
    recipe_names: dict[int, str]


def import_graph_data(
    session_factory: sessionmaker[Session],
    uri: str,
    user: str,
    password: str,
    progress_callback: GraphImportProgressCallback | None = None,
) -> dict[str, Any]:
    """从 PostgreSQL 同步菜品/食材/概念到 Neo4j，幂等 MERGE。"""

    driver = create_neo4j_driver(uri, user, password)
    try:
        try:
            source_data = _load_source_data(
                session_factory,
                progress_callback,
            )
        except Exception as exc:
            raise GraphImportError(500, f"读取 PostgreSQL 失败：{exc}") from exc

        grouped_tags = _build_grouped_tags()
        try:
            with driver.session() as session:
                _merge_recipes(
                    session,
                    source_data.recipes,
                    grouped_tags,
                    progress_callback,
                )
                _merge_ingredients(
                    session,
                    source_data.ingredients,
                    progress_callback,
                )
                _merge_recipe_ingredients(
                    session,
                    source_data.recipe_ingredients,
                    source_data.ingredient_names,
                    source_data.recipe_names,
                    progress_callback,
                )
                _merge_concepts(session, progress_callback)
        except Exception as exc:
            raise GraphImportError(500, f"写入 Neo4j 失败：{exc}") from exc
        return {
            "recipes": len(source_data.recipes),
            "ingredients": len(source_data.ingredients),
            "recipe_ingredients": len(source_data.recipe_ingredients),
        }
    finally:
        driver.close()


def _build_grouped_tags() -> list[str]:
    """入组标签全集（5 组 23 个）。"""
    return [
        tag for group_tags in GROUP_TAGS.values() for tag in group_tags
    ]


def _load_source_data(
    session_factory: sessionmaker[Session],
    progress_callback: GraphImportProgressCallback | None,
) -> _GraphSourceData:
    """一次读取图同步所需的 PostgreSQL 数据。"""
    _report_progress(progress_callback, "读取 PostgreSQL", 0, 3, is_forced=True)
    with session_factory() as session:
        recipes = list(session.scalars(select(Recipe)))
        _report_progress(
            progress_callback,
            "读取 PostgreSQL",
            1,
            3,
            is_forced=True,
        )
        ingredients = list(session.scalars(select(Ingredient)))
        _report_progress(
            progress_callback,
            "读取 PostgreSQL",
            2,
            3,
            is_forced=True,
        )
        recipe_ingredients = list(session.scalars(select(RecipeIngredient)))
    _report_progress(progress_callback, "读取 PostgreSQL", 3, 3, is_forced=True)
    return _GraphSourceData(
        recipes=recipes,
        ingredients=ingredients,
        recipe_ingredients=recipe_ingredients,
        ingredient_names={item.id: item.name for item in ingredients},
        recipe_names={item.id: item.name for item in recipes},
    )


def _report_progress(
    progress_callback: GraphImportProgressCallback | None,
    stage: str,
    completed: int,
    total: int,
    *,
    is_forced: bool = False,
) -> None:
    """按固定间隔报告进度，并确保每个阶段报告开始与完成。"""
    if progress_callback is None:
        return
    if (
        is_forced
        or completed == 0
        or completed == total
        or completed % _PROGRESS_INTERVAL == 0
    ):
        progress_callback(stage, completed, total)


def _iter_with_progress(
    items: list[_Item],
    stage: str,
    progress_callback: GraphImportProgressCallback | None,
) -> Iterator[_Item]:
    """遍历一组数据，并按统一规则报告开始、间隔和完成进度。"""
    total = len(items)
    _report_progress(progress_callback, stage, 0, total)
    for completed, item in enumerate(items, start=1):
        yield item
        _report_progress(progress_callback, stage, completed, total)


def _merge_recipes(
    session: Any,
    recipes: list[Recipe],
    grouped_tags: list[str],
    progress_callback: GraphImportProgressCallback | None = None,
) -> None:
    for recipe in _iter_with_progress(
        recipes,
        "写入菜谱节点",
        progress_callback,
    ):
        tags = [tag for tag in recipe.labels if tag in grouped_tags]
        session.run(
            """
            MERGE (r:Recipe {name: $name})
            ON CREATE SET r.dish_type = $dish_type
            SET r.tags = $tags,
                r.total_time_lower_bound_minutes = $total_time,
                r.dish_type = $dish_type,
                r.difficulty = $difficulty,
                r.is_recommendable = $is_recommendable
            """,
            name=recipe.name,
            tags=tags,
            total_time=recipe.total_time_lower_bound_minutes,
            dish_type=recipe.dish_type,
            difficulty=recipe.difficulty,
            is_recommendable=recipe.is_recommendable,
        )


def _merge_ingredients(
    session: Any,
    ingredients: list[Ingredient],
    progress_callback: GraphImportProgressCallback | None = None,
) -> None:
    for ingredient in _iter_with_progress(
        ingredients,
        "写入食材节点",
        progress_callback,
    ):
        session.run(
            """
            MERGE (i:Ingredient {name: $name})
            SET i.category = $category,
                i.is_core_ingredient = $is_core
            """,
            name=ingredient.name,
            category=ingredient.category,
            is_core=ingredient.name not in AUXILIARY_INGREDIENTS,
        )


def _merge_recipe_ingredients(
    session: Any,
    recipe_ingredients: list[RecipeIngredient],
    ingredient_names: dict[int, str],
    recipe_names: dict[int, str],
    progress_callback: GraphImportProgressCallback | None = None,
) -> None:
    for relation in _iter_with_progress(
        recipe_ingredients,
        "写入菜谱食材关系",
        progress_callback,
    ):
        ingredient_name = ingredient_names.get(relation.ingredient_id)
        recipe_name = recipe_names.get(relation.recipe_id)
        if ingredient_name is not None and recipe_name is not None:
            session.run(
                """
                MATCH (i:Ingredient {name: $ingredient_name}),
                      (r:Recipe {name: $recipe_name})
                MERGE (i)-[:part_of]->(r)
                """,
                ingredient_name=ingredient_name,
                recipe_name=recipe_name,
            )


def _merge_concepts(
    session: Any,
    progress_callback: GraphImportProgressCallback | None = None,
) -> None:
    """写入过敏类目与概念的 is_a 关系（预置数据）。"""
    total = len(ALLERGEN_CONCEPT_MEMBERS) + sum(
        len(members) for members in ALLERGEN_CONCEPT_MEMBERS.values()
    )
    completed = 0
    _report_progress(progress_callback, "写入过敏概念关系", completed, total)
    for concept_name, members in ALLERGEN_CONCEPT_MEMBERS.items():
        session.run(
            """
            MERGE (c:Concept {name: $name})
            SET c.kind = $kind
            """,
            name=concept_name,
            kind=CONCEPT_KINDS[concept_name],
        )
        completed += 1
        _report_progress(
            progress_callback,
            "写入过敏概念关系",
            completed,
            total,
        )
        for member in members:
            session.run(
                """
                MATCH (i:Ingredient {name: $member}),
                      (c:Concept {name: $concept_name})
                MERGE (i)-[:is_a]->(c)
                """,
                member=member,
                concept_name=concept_name,
            )
            completed += 1
            _report_progress(
                progress_callback,
                "写入过敏概念关系",
                completed,
                total,
            )


__all__ = [
    "GraphImportError",
    "GraphImportProgressCallback",
    "import_graph_data",
]
