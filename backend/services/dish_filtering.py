from __future__ import annotations

import copy
from typing import Any

from backend.core.constraint_integration_contract import (
    IntegratedConstraints,
    IntegratedDish,
)
from backend.core.dish_filtering_contract import (
    ALL_ALLERGEN_MEMBERS,
    ALLERGEN_CONCEPT_MEMBERS,
    DishFilteringExecutionError,
    DishFilteringResult,
    DishFilteringValidationError,
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

        self._validate(constraints)
        if constraints["has_conflicts"]:
            raise _invalid("存在冲突的约束必须先经用户确认，不能直接筛选")

        allergen_members, unmatched_allergens = self._expand_allergens(
            constraints["allergens"]
        )
        dishes: list[list[RecipeMatch]] = []
        for dish in constraints["dishes"]:
            dishes.append(self._filter_dish(dish, allergen_members, constraints))
        return {
            "dishes": dishes,
            "unmatched_allergens": unmatched_allergens,
        }

    # ---- 校验 ----

    def _validate(self, constraints: object) -> None:
        validate_integrated_constraints(constraints)

    # ---- 过敏展开 ----

    def _expand_allergens(
        self, allergens: list[str]
    ) -> tuple[list[str], list[str]]:
        """将过敏词展开为排除食材集合；无法展开的词进入 unmatched。"""
        members: list[str] = []
        unmatched: list[str] = []
        for allergen in allergens:
            if allergen in ALLERGEN_CONCEPT_MEMBERS:
                # 概念词：展开为全部成员
                members.extend(ALLERGEN_CONCEPT_MEMBERS[allergen])
            elif allergen in ALL_ALLERGEN_MEMBERS:
                # 食材词：直接排除该食材
                members.append(allergen)
            else:
                unmatched.append(allergen)
        return members, unmatched

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
        for record in records:
            tags = list(record["matched_tags"])
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
        return {
            "meal_periods": list(constraints["meal_periods"]),
            "pos_taste": pos_taste,
            "neg_taste": neg_taste,
            "cuisines": list(dish["cuisines"]),
            "effects": list(dish["effects"]),
            "pops": list(dish["special_populations"]),
            "max_total_time_minutes": constraints[
                "max_total_time_minutes"
            ],
            "requirements": copy.deepcopy(dish["required_ingredients"]),
            "excluded": allergen_members,
            "available_ingredients": list(
                constraints["available_ingredients"]
            ),
        }

    def _build_query(self, params: dict[str, Any]) -> str:
        # 值全部走参数；仅按固定 kind 分支拼接结构，不含用户输入
        requirement_clauses = []
        for index, requirement in enumerate(params["requirements"]):
            kind = requirement["kind"]
            param_key = f"req_{index}"
            if kind == "ingredient":
                clause = (
                    "EXISTS((:Ingredient "
                    f"{{name: ${param_key}}})-[:part_of]->(d))"
                )
            elif kind == "category":
                clause = (
                    "EXISTS((:Ingredient "
                    f"{{category: ${param_key}}})-[:part_of]->(d))"
                )
            else:  # concept：菜的某个食材 is_a 该概念
                clause = (
                    "EXISTS((d)<-[:part_of]-(:Ingredient)-[:is_a]->"
                    f"(:Concept {{name: ${param_key}}}))"
                )
            requirement_clauses.append(clause)
            params[param_key] = requirement["value"]

        requirements_where = " AND ".join(requirement_clauses) or "TRUE"

        available_where = "TRUE"
        if params["available_ingredients"]:
            available_where = (
                "all(i IN [(ing:Ingredient)-[:part_of]->(d) WHERE "
                "ing.is_core_ingredient = true | ing.name] "
                "WHERE i IN $available_ingredients)"
            )

        max_time_where = "TRUE"
        if params["max_total_time_minutes"] is not None:
            max_time_where = (
                "d.total_time_lower_bound_minutes <= "
                "$max_total_time_minutes"
            )

        return f"""
MATCH (i:Ingredient)-[:part_of]->(d:Recipe)
WHERE ($meal_periods = [] OR any(x IN $meal_periods WHERE x IN d.tags))
  AND all(p IN $pos_taste WHERE p IN d.tags)
  AND NOT any(n IN $neg_taste WHERE n IN d.tags)
  AND ($cuisines = [] OR any(x IN $cuisines WHERE x IN d.tags))
  AND ($effects = [] OR any(x IN $effects WHERE x IN d.tags))
  AND ($pops = [] OR any(x IN $pops WHERE x IN d.tags))
  AND {max_time_where}
  AND {requirements_where}
  AND {available_where}
  AND NOT any(e IN $excluded WHERE EXISTS(
      (:Ingredient {{name: e}})-[:part_of]->(d)))
RETURN DISTINCT d.name AS recipe_name,
       d.dish_type AS recipe_type,
       d.tags AS matched_tags
ORDER BY size(d.tags) DESC, d.name ASC
"""


def _derive_groups(tags: list[str]) -> list[str]:
    """从命中标签推导所属组名（噪声标签无组，忽略）。"""
    matched_groups = {TAG_TO_GROUP[tag] for tag in tags if tag in TAG_TO_GROUP}
    return [group for group in TAG_GROUPS if group in matched_groups]


__all__ = [
    "DishFilteringExecutionError",
    "DishFilteringService",
    "DishFilteringValidationError",
]
