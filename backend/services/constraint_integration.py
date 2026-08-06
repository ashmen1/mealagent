from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from backend.core.constraint_input_validation import (
    validate_integration_inputs,
)
from backend.core.constraint_integration_contract import (
    ConstraintConflict,
    ConstraintIntegrationError,
    ConstraintIntegrationValidationError,
    IntegratedConstraints,
    IntegratedDish,
)


class ConstraintIntegrationService:
    """整合健康档案约束与单轮对话约束。"""

    def integrate(
        self,
        profile_constraints: Mapping[str, Any],
        dialogue_constraints: Mapping[str, Any],
    ) -> IntegratedConstraints:
        """校验并整合两组已提取约束。"""

        validate_integration_inputs(profile_constraints, dialogue_constraints)
        dishes, conflicts = _integrate_dishes(
            profile_constraints,
            dialogue_constraints,
        )
        return _build_result(
            profile_constraints,
            dialogue_constraints,
            dishes,
            conflicts,
        )


def _integrate_dishes(
    profile: Mapping[str, Any],
    dialogue: Mapping[str, Any],
) -> tuple[list[IntegratedDish], list[ConstraintConflict]]:
    dishes: list[IntegratedDish] = []
    conflicts: list[ConstraintConflict] = []
    for dish_index, source_dish in enumerate(dialogue["dishes"]):
        dishes.append(_integrate_dish(profile, source_dish))
        conflicts.extend(
            _collect_conflicts(
                profile["allergens"],
                source_dish,
                dish_index,
                dialogue["evidence"],
            )
        )
    return dishes, conflicts


def _integrate_dish(
    profile: Mapping[str, Any],
    source_dish: Mapping[str, Any],
) -> IntegratedDish:
    tastes = dict(profile["taste_preferences"])
    tastes.update(source_dish["taste_preferences"])

    return {
        "count": source_dish["count"],
        "dish_type": source_dish["dish_type"],
        "taste_preferences": tastes,
        "cuisines": copy.deepcopy(source_dish["cuisines"]),
        "effects": copy.deepcopy(source_dish["effects"]),
        "special_populations": _merge_unique(
            profile["special_populations"],
            source_dish["special_populations"],
        ),
        "required_ingredients": copy.deepcopy(
            source_dish["required_ingredients"]
        ),
    }


def _merge_unique(first: list[str], second: list[str]) -> list[str]:
    merged = list(first)
    seen = set(first)
    for value in second:
        if value not in seen:
            merged.append(value)
            seen.add(value)
    return merged


def _collect_conflicts(
    allergens: list[str],
    dish: Mapping[str, Any],
    dish_index: int,
    evidence: Mapping[str, str],
) -> list[ConstraintConflict]:
    conflicts: list[ConstraintConflict] = []
    for requirement_index, requirement in enumerate(
        dish["required_ingredients"]
    ):
        dialogue_path = _build_requirement_path(
            dish_index,
            requirement_index,
        )
        for allergen_index, allergen in enumerate(allergens):
            if allergen == requirement["value"]:
                conflicts.append(
                    _build_conflict(
                        allergen,
                        allergen_index,
                        requirement,
                        dish_index,
                        dialogue_path,
                        evidence[dialogue_path],
                    )
                )
    return conflicts


def _build_requirement_path(dish_index: int, requirement_index: int) -> str:
    return (
        f"dishes[{dish_index}].required_ingredients["
        f"{requirement_index}].value"
    )


def _build_conflict(
    allergen: str,
    allergen_index: int,
    requirement: Mapping[str, str],
    dish_index: int,
    dialogue_path: str,
    dialogue_evidence: str,
) -> ConstraintConflict:
    return {
        "code": "allergen_required_ingredient",
        "dish_index": dish_index,
        "profile_path": f"allergens[{allergen_index}]",
        "dialogue_path": dialogue_path,
        "allergen": allergen,
        "required_ingredient": copy.deepcopy(requirement),
        "dialogue_evidence": dialogue_evidence,
    }


def _build_result(
    profile: Mapping[str, Any],
    dialogue: Mapping[str, Any],
    dishes: list[IntegratedDish],
    conflicts: list[ConstraintConflict],
) -> IntegratedConstraints:
    return {
        "profile_id": profile["profile_id"],
        "dialogue_id": dialogue["dialogue_id"],
        "meal_periods": copy.deepcopy(dialogue["meal_periods"]),
        "diner_count": dialogue["diner_count"],
        "max_total_time_minutes": dialogue["max_total_time_minutes"],
        "available_ingredients": copy.deepcopy(
            dialogue["available_ingredients"]
        ),
        "allergens": copy.deepcopy(profile["allergens"]),
        "dishes": dishes,
        "has_conflicts": bool(conflicts),
        "conflicts": conflicts,
    }


__all__ = [
    "ConstraintIntegrationError",
    "ConstraintIntegrationService",
    "ConstraintIntegrationValidationError",
    "IntegratedConstraints",
]

