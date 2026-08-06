from __future__ import annotations

from typing import Any

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


def import_graph_data(
    session_factory: sessionmaker[Session],
    uri: str,
    user: str,
    password: str,
) -> dict[str, Any]:
    """从 PostgreSQL 同步菜品/食材/概念到 Neo4j，幂等 MERGE。"""

    driver = create_neo4j_driver(uri, user, password)
    try:
        try:
            with session_factory() as session:
                recipes = list(session.scalars(select(Recipe)))
                ingredients = list(session.scalars(select(Ingredient)))
                recipe_ingredients = list(
                    session.scalars(select(RecipeIngredient))
                )
                ingredient_names = {
                    ingredient.id: ingredient.name
                    for ingredient in ingredients
                }
                recipe_names = {
                    recipe.id: recipe.name for recipe in recipes
                }
        except Exception as exc:
            raise GraphImportError(500, f"读取 PostgreSQL 失败：{exc}") from exc

        grouped_tags = _build_grouped_tags()
        try:
            with driver.session() as session:
                _merge_recipes(session, recipes, grouped_tags)
                _merge_ingredients(session, ingredients)
                _merge_recipe_ingredients(
                    session,
                    recipe_ingredients,
                    ingredient_names,
                    recipe_names,
                )
                _merge_concepts(session)
        except Exception as exc:
            raise GraphImportError(500, f"写入 Neo4j 失败：{exc}") from exc
        return {
            "recipes": len(recipes),
            "ingredients": len(ingredients),
            "recipe_ingredients": len(recipe_ingredients),
        }
    finally:
        driver.close()


def _build_grouped_tags() -> list[str]:
    """入组标签全集（5 组 23 个）。"""
    return [
        tag for group_tags in GROUP_TAGS.values() for tag in group_tags
    ]


def _merge_recipes(
    session: Any,
    recipes: list[Recipe],
    grouped_tags: list[str],
) -> None:
    for recipe in recipes:
        tags = [tag for tag in recipe.labels if tag in grouped_tags]
        session.run(
            """
            MERGE (r:Recipe {name: $name})
            ON CREATE SET r.dish_type = $dish_type
            SET r.tags = $tags,
                r.total_time_lower_bound_minutes = $total_time,
                r.dish_type = $dish_type
            """,
            name=recipe.name,
            tags=tags,
            total_time=recipe.total_time_lower_bound_minutes,
            dish_type=recipe.dish_type,
        )


def _merge_ingredients(
    session: Any,
    ingredients: list[Ingredient],
) -> None:
    for ingredient in ingredients:
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
) -> None:
    for relation in recipe_ingredients:
        ingredient_name = ingredient_names.get(relation.ingredient_id)
        recipe_name = recipe_names.get(relation.recipe_id)
        if ingredient_name is None or recipe_name is None:
            continue
        session.run(
            """
            MATCH (i:Ingredient {name: $ingredient_name}),
                  (r:Recipe {name: $recipe_name})
            MERGE (i)-[:part_of]->(r)
            """,
            ingredient_name=ingredient_name,
            recipe_name=recipe_name,
        )


def _merge_concepts(session: Any) -> None:
    """写入过敏类目与概念的 is_a 关系（预置数据）。"""
    for concept_name, members in ALLERGEN_CONCEPT_MEMBERS.items():
        session.run(
            """
            MERGE (c:Concept {name: $name})
            SET c.kind = $kind
            """,
            name=concept_name,
            kind=CONCEPT_KINDS[concept_name],
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


__all__ = [
    "GraphImportError",
    "import_graph_data",
]
