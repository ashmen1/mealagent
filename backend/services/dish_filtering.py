from __future__ import annotations

import copy
from typing import Any

from backend.core.constraint_integration_contract import (
    IntegratedConstraints,
    IntegratedDish,
)
from backend.core.dish_filtering_contract import (
    ALLOWED_DIFFICULTIES_BY_MAX,
    ALLERGEN_CONCEPT_MEMBERS,
    DishFilteringExecutionError,
    DishFilteringResult,
    DishFilteringValidationError,
    GROUP_TAGS,
    RecipeMatch,
    TAG_GROUPS,
    TAG_TO_GROUP,
    TASTE_KEY_TO_TAG,
)
from backend.core.dish_filtering_validation import (
    validate_integrated_constraints,
)


def _invalid(message: str) -> DishFilteringValidationError:
    return DishFilteringValidationError(400, message)


def _execution_error(message: str) -> DishFilteringExecutionError:
    return DishFilteringExecutionError(500, message)


class DishFilteringService:
    """按整合约束在 Neo4j 中筛选菜谱候选。"""

    def __init__(self, neo4j_driver: Any) -> None:
        self._driver = neo4j_driver

    def filter(
        self,
        constraints: IntegratedConstraints,
    ) -> DishFilteringResult:
        """校验并执行筛选，返回每组需求的菜谱候选。"""

        validate_integrated_constraints(constraints)
        if constraints["has_conflicts"]:
            raise _invalid("存在冲突的约束必须先经用户确认，不能直接筛选")

        direct_allergens = [
            allergen
            for allergen in constraints["allergens"]
            if allergen not in ALLERGEN_CONCEPT_MEMBERS
        ]
        standard_ingredient_names = self._load_standard_ingredient_names(
            direct_allergens
        )
        allergen_members, unmatched_allergens = self._expand_allergens(
            constraints["allergens"], standard_ingredient_names
        )
        dishes: list[list[RecipeMatch]] = []
        for dish in constraints["dishes"]:
            dishes.append(self._filter_dish(dish, allergen_members, constraints))
        return {
            "dishes": dishes,
            "unmatched_allergens": unmatched_allergens,
        }

    # ---- 过敏展开 ----

    def _expand_allergens(
        self,
        allergens: list[str],
        standard_ingredient_names: set[str],
    ) -> tuple[list[str], list[str]]:
        """将过敏词展开为排除食材集合；无法展开的词进入 unmatched。"""
        members: list[str] = []
        unmatched: list[str] = []
        for allergen in allergens:
            if allergen in ALLERGEN_CONCEPT_MEMBERS:
                # 概念词：展开为全部成员
                members.extend(ALLERGEN_CONCEPT_MEMBERS[allergen])
            elif allergen in standard_ingredient_names:
                # 食材词：直接排除该食材
                members.append(allergen)
            else:
                unmatched.append(allergen)
        return members, unmatched

    def _load_standard_ingredient_names(
        self, ingredient_names: list[str]
    ) -> set[str]:
        """从图中确认直接过敏词是否为标准食材名。"""
        if not ingredient_names:
            return set()
        query = """
MATCH (ingredient:Ingredient)
WHERE ingredient.name IN $ingredient_names
RETURN DISTINCT ingredient.name AS ingredient_name
"""
        try:
            with self._driver.session() as session:
                records = list(
                    session.run(query, ingredient_names=ingredient_names)
                )
        except Exception as exc:
            raise _execution_error(f"Neo4j 查询失败：{exc}") from exc
        return {record["ingredient_name"] for record in records}

    # ---- 单组过滤 ----

    def _filter_dish(
        self,
        dish: IntegratedDish,
        allergen_members: list[str],
        constraints: IntegratedConstraints,
    ) -> list[RecipeMatch]:
        params = self._build_params(dish, allergen_members, constraints)
        query = self._build_query(params)
        try:
            with self._driver.session() as session:
                result = session.run(query, **params)
                records = list(result)
        except Exception as exc:
            raise _execution_error(f"Neo4j 查询失败：{exc}") from exc

        matches: list[RecipeMatch] = []
        requested_tags = set(params["requested_tags"])
        for record in records:
            tags = [
                tag
                for tag in record["matched_tags"]
                if tag in requested_tags
            ]
            matches.append(
                {
                    "recipe_name": record["recipe_name"],
                    "recipe_type": record["recipe_type"],
                    "matched_tags": tags,
                    "matched_groups": _derive_groups(tags),
                }
            )
        # 命中标签数降序；同数时保持图返回顺序（确定性）
        matches.sort(key=lambda m: -len(m["matched_tags"]))
        return matches

    def _build_params(
        self,
        dish: IntegratedDish,
        allergen_members: list[str],
        constraints: IntegratedConstraints,
    ) -> dict[str, Any]:
        tastes = dish["taste_preferences"]
        pos_taste = [
            TASTE_KEY_TO_TAG[key]
            for key, enabled in tastes.items()
            if enabled
        ]
        neg_taste = [
            TASTE_KEY_TO_TAG[key]
            for key, enabled in tastes.items()
            if not enabled
        ]
        # 人群只保留有标签对应的（孕妇等档案人群无标签，交给营养线）
        filterable_pops = [
            value
            for value in dish["special_populations"]
            if value in GROUP_TAGS["人群"]
        ]
        requested_tags = _ordered_unique(
            list(constraints["meal_periods"])
            + pos_taste
            + list(dish["cuisines"])
            + list(dish["effects"])
            + filterable_pops
        )
        max_difficulty = constraints["max_difficulty"]
        allowed_difficulties = (
            None
            if max_difficulty is None
            else list(ALLOWED_DIFFICULTIES_BY_MAX[max_difficulty])
        )
        return {
            "meal_periods": list(constraints["meal_periods"]),
            "pos_taste": pos_taste,
            "neg_taste": neg_taste,
            "cuisines": list(dish["cuisines"]),
            "effects": list(dish["effects"]),
            "pops": filterable_pops,
            "requested_tags": requested_tags,
            "dish_type": dish["dish_type"],
            "max_total_time_minutes": constraints[
                "max_total_time_minutes"
            ],
            "allowed_difficulties": allowed_difficulties,
            "requirement_groups": copy.deepcopy(
                dish["required_ingredient_groups"]
            ),
            "excluded": allergen_members,
            "available_ingredients": list(
                constraints["available_ingredients"]
            ),
        }

    def _build_query(self, params: dict[str, Any]) -> str:
        # 值全部走参数；仅按固定 kind 分支拼接结构，不含用户输入
        clauses = [_fixed_clauses()]
        clauses.extend(_build_requirement_clauses(params))
        if params["available_ingredients"]:
            clauses.append(
                "(NOT EXISTS { MATCH (available:Ingredient) "
                "WHERE available.name IN $available_ingredients } OR "
                "all(i IN [(ing:Ingredient)-[:part_of]->(d) WHERE "
                "ing.is_core_ingredient = true | ing.name] "
                "WHERE i IN $available_ingredients))"
            )
        if params["max_total_time_minutes"] is not None:
            clauses.append(
                "d.total_time_lower_bound_minutes <= "
                "$max_total_time_minutes"
            )
        if params["allowed_difficulties"] is not None:
            clauses.append("d.difficulty IN $allowed_difficulties")
        if params["dish_type"] != "未指定":
            clauses.append("d.dish_type = $dish_type")

        return f"""
MATCH (i:Ingredient)-[:part_of]->(d:Recipe)
WHERE {" AND ".join(clauses)}
  AND NOT any(e IN $excluded WHERE EXISTS(
      (:Ingredient {{name: e}})-[:part_of]->(d)))
RETURN DISTINCT d.name AS recipe_name,
       d.dish_type AS recipe_type,
       [tag IN d.tags WHERE tag IN $requested_tags] AS matched_tags
ORDER BY size([tag IN d.tags WHERE tag IN $requested_tags]) DESC, d.name ASC
"""


def _fixed_clauses() -> str:
    """固定不变的标签约束片段。"""
    return (
        "($meal_periods = [] OR any(x IN $meal_periods WHERE x IN d.tags))"
        " AND all(p IN $pos_taste WHERE p IN d.tags)"
        " AND NOT any(n IN $neg_taste WHERE n IN d.tags)"
        " AND ($cuisines = [] OR any(x IN $cuisines WHERE x IN d.tags))"
        " AND ($effects = [] OR any(x IN $effects WHERE x IN d.tags))"
        " AND ($pops = [] OR any(x IN $pops WHERE x IN d.tags))"
    )


def _build_requirement_clauses(params: dict[str, Any]) -> list[str]:
    """按组关系和kind生成食材EXISTS片段，所有值均走参数。"""
    clauses: list[str] = []
    for group_index, group in enumerate(params["requirement_groups"]):
        item_clauses: list[str] = []
        for item_index, requirement in enumerate(group["items"]):
            param_key = f"req_{group_index}_{item_index}"
            item_clauses.append(
                _build_requirement_expression(requirement["kind"], param_key)
            )
            params[param_key] = requirement["value"]
        if group["match"] == "all":
            clauses.extend(item_clauses)
        else:
            clauses.append(f"({' OR '.join(item_clauses)})")
    return clauses


def _build_requirement_expression(kind: str, param_key: str) -> str:
    if kind == "ingredient":
        return (
            "EXISTS((:Ingredient "
            f"{{name: ${param_key}}})-[:part_of]->(d))"
        )
    if kind == "category":
        return (
            "EXISTS((:Ingredient "
            f"{{category: ${param_key}}})-[:part_of]->(d))"
        )
    return (
        "EXISTS((d)<-[:part_of]-(:Ingredient)-[:is_a]->"
        f"(:Concept {{name: ${param_key}}}))"
    )


def _derive_groups(tags: list[str]) -> list[str]:
    """从命中标签推导所属组名（噪声标签无组，忽略）。"""
    matched_groups = {TAG_TO_GROUP[tag] for tag in tags if tag in TAG_TO_GROUP}
    return [group for group in TAG_GROUPS if group in matched_groups]


def _ordered_unique(values: list[str]) -> list[str]:
    """按首次出现顺序去重。"""
    return list(dict.fromkeys(values))


__all__ = [
    "DishFilteringExecutionError",
    "DishFilteringService",
    "DishFilteringValidationError",
]
