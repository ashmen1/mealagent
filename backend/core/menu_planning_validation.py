from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, NoReturn, cast

from backend.core.dialogue_constraint_contract import DISH_TYPES
from backend.core.menu_planning_contract import (
    MenuPlanningError,
    MenuPlanningInput,
    NUTRIENT_FIELDS,
)
from backend.core.nutrition_contract import MEAL_PERIODS


TOP_LEVEL_FIELDS = (
    "profile_id",
    "dialogue_id",
    "meal_period",
    "diner_count",
    "total_dish_count",
    "special_populations",
    "dishes",
    "nutrient_targets",
    "unmatched_allergens",
)
DISH_FIELDS = ("count", "dish_type", "candidates")
CANDIDATE_FIELDS = (
    "recipe_name",
    "recipe_type",
    "matched_tags",
    "nutrition",
)
TARGET_FIELDS = (
    "status",
    "unit",
    "target_value",
    "lower_bound",
    "upper_bound",
    "target_basis",
    "lower_basis",
    "upper_basis",
)
EXPECTED_UNITS = {
    "energy_kcal": "kcal",
    "protein_g": "g",
    "fat_g": "g",
    "carbohydrate_g": "g",
    "fiber_g": "g",
    "sodium_mg": "mg",
    "calcium_mg": "mg",
    "iron_mg": "mg",
    "cholesterol_mg": "mg",
}
REQUIRED_TARGET_VALUES = {
    "energy_kcal": ("target_value",),
    "protein_g": ("target_value", "lower_bound", "upper_bound"),
    "fat_g": ("lower_bound", "upper_bound"),
    "carbohydrate_g": ("lower_bound", "upper_bound"),
    "fiber_g": ("lower_bound", "upper_bound"),
    "sodium_mg": ("target_value", "upper_bound"),
    "calcium_mg": ("target_value", "upper_bound"),
    "iron_mg": ("target_value", "upper_bound"),
}


def validate_menu_planning_input(value: object) -> MenuPlanningInput:
    """校验并将营养数值统一为 Decimal。"""

    source = _require_mapping(value, "planning_input")
    _require_exact_fields(source, TOP_LEVEL_FIELDS, "planning_input")
    _validate_positive_integer(source["profile_id"], "profile_id")
    _validate_positive_integer(source["dialogue_id"], "dialogue_id")
    if source["meal_period"] not in MEAL_PERIODS:
        _invalid("meal_period 只允许早餐、午餐、晚餐")
    _validate_optional_positive_integer(source["diner_count"], "diner_count")
    _validate_optional_positive_integer(
        source["total_dish_count"], "total_dish_count"
    )
    special_populations = _validate_string_array(
        source["special_populations"], "special_populations"
    )
    unmatched_allergens = _validate_string_array(
        source["unmatched_allergens"], "unmatched_allergens"
    )
    dishes = _validate_dishes(source["dishes"])
    _validate_dish_count_structure(source["total_dish_count"], dishes)
    targets = _validate_targets(source["nutrient_targets"])

    return cast(
        MenuPlanningInput,
        {
            "profile_id": source["profile_id"],
            "dialogue_id": source["dialogue_id"],
            "meal_period": source["meal_period"],
            "diner_count": source["diner_count"],
            "total_dish_count": source["total_dish_count"],
            "special_populations": special_populations,
            "dishes": dishes,
            "nutrient_targets": targets,
            "unmatched_allergens": unmatched_allergens,
        },
    )


def _validate_dishes(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        _invalid("dishes 必须是非空数组")
    result: list[dict[str, Any]] = []
    for dish_index, dish_value in enumerate(value):
        location = f"dishes[{dish_index}]"
        dish = _require_mapping(dish_value, location)
        _require_exact_fields(dish, DISH_FIELDS, location)
        _validate_optional_positive_integer(dish["count"], f"{location}.count")
        if dish["dish_type"] not in DISH_TYPES:
            _invalid(f"{location}.dish_type不在允许值中")
        candidates = _validate_candidates(dish["candidates"], location)
        result.append(
            {
                "count": dish["count"],
                "dish_type": dish["dish_type"],
                "candidates": candidates,
            }
        )
    return result


def _validate_dish_count_structure(
    total_dish_count: object,
    dishes: list[dict[str, Any]],
) -> None:
    """总数明确时，组内数量必须为其保留出可行分配空间。"""

    if total_dish_count is None:
        return
    explicit_total = sum(
        dish["count"] for dish in dishes if dish["count"] is not None
    )
    unspecified_count = sum(
        1 for dish in dishes if dish["count"] is None
    )
    if explicit_total + unspecified_count > total_dish_count:
        _invalid("total_dish_count与dishes组内数量矛盾")
    if unspecified_count == 0 and explicit_total != total_dish_count:
        _invalid("total_dish_count必须等于全部明确组数量之和")


def _validate_candidates(value: object, dish_location: str) -> list[dict[str, Any]]:
    location = f"{dish_location}.candidates"
    if not isinstance(value, list):
        _invalid(f"{location}必须是数组")
    result: list[dict[str, Any]] = []
    recipe_names: set[str] = set()
    for candidate_index, candidate_value in enumerate(value):
        candidate_location = f"{location}[{candidate_index}]"
        candidate = _require_mapping(candidate_value, candidate_location)
        _require_exact_fields(candidate, CANDIDATE_FIELDS, candidate_location)
        recipe_name = candidate["recipe_name"]
        if not isinstance(recipe_name, str) or not recipe_name.strip():
            _invalid(f"{candidate_location}.recipe_name必须是非空字符串")
        if recipe_name in recipe_names:
            _invalid(f"{location}中菜谱名不得重复：{recipe_name}")
        recipe_names.add(recipe_name)
        recipe_type = candidate["recipe_type"]
        if recipe_type is not None and (
            not isinstance(recipe_type, str) or not recipe_type.strip()
        ):
            _invalid(f"{candidate_location}.recipe_type必须是非空字符串或null")
        matched_tags = _validate_string_array(
            candidate["matched_tags"], f"{candidate_location}.matched_tags"
        )
        nutrition = _validate_nutrition(
            candidate["nutrition"], f"{candidate_location}.nutrition"
        )
        result.append(
            {
                "recipe_name": recipe_name,
                "recipe_type": recipe_type,
                "matched_tags": matched_tags,
                "nutrition": nutrition,
            }
        )
    return result


def _validate_nutrition(value: object, location: str) -> dict[str, Decimal]:
    nutrition = _require_mapping(value, location)
    _require_exact_fields(nutrition, NUTRIENT_FIELDS, location)
    return {
        field: _validate_nonnegative_decimal(
            nutrition[field], f"{location}.{field}"
        )
        for field in NUTRIENT_FIELDS
    }


def _validate_targets(value: object) -> dict[str, dict[str, Any]]:
    targets = _require_mapping(value, "nutrient_targets")
    _require_exact_fields(targets, NUTRIENT_FIELDS, "nutrient_targets")
    result: dict[str, dict[str, Any]] = {}
    for nutrient in NUTRIENT_FIELDS:
        location = f"nutrient_targets.{nutrient}"
        target = _require_mapping(targets[nutrient], location)
        _require_exact_fields(target, TARGET_FIELDS, location)
        status = target["status"]
        if status not in {"available", "not_established"}:
            _invalid(f"{location}.status不在允许值中")
        if target["unit"] != EXPECTED_UNITS[nutrient]:
            _invalid(f"{location}.unit与营养素不匹配")
        normalized = {
            "status": status,
            "unit": target["unit"],
            "target_value": _validate_optional_decimal(
                target["target_value"], f"{location}.target_value"
            ),
            "lower_bound": _validate_optional_decimal(
                target["lower_bound"], f"{location}.lower_bound"
            ),
            "upper_bound": _validate_optional_decimal(
                target["upper_bound"], f"{location}.upper_bound"
            ),
            "target_basis": _validate_optional_string(
                target["target_basis"], f"{location}.target_basis"
            ),
            "lower_basis": _validate_optional_string(
                target["lower_basis"], f"{location}.lower_basis"
            ),
            "upper_basis": _validate_optional_string(
                target["upper_basis"], f"{location}.upper_basis"
            ),
        }
        if status == "not_established":
            if nutrient != "cholesterol_mg" or any(
                normalized[field] is not None for field in TARGET_FIELDS[2:]
            ):
                _invalid(f"{location}的not_established目标必须为空")
        else:
            for field in REQUIRED_TARGET_VALUES.get(nutrient, ()):
                if normalized[field] is None:
                    _invalid(f"{location}.{field}必填")
            lower = normalized["lower_bound"]
            upper = normalized["upper_bound"]
            if lower is not None and upper is not None and upper < lower:
                _invalid(f"{location}的上界不得小于下界")
        result[nutrient] = normalized
    return result


def _validate_positive_integer(value: object, location: str) -> None:
    if type(value) is not int or value <= 0:
        _invalid(f"{location}必须是正整数")


def _validate_optional_positive_integer(value: object, location: str) -> None:
    if value is not None:
        _validate_positive_integer(value, location)


def _validate_string_array(value: object, location: str) -> list[str]:
    if not isinstance(value, list):
        _invalid(f"{location}必须是数组")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        _invalid(f"{location}只能包含非空字符串")
    if len(set(value)) != len(value):
        _invalid(f"{location}不得重复")
    return list(value)


def _validate_optional_decimal(value: object, location: str) -> Decimal | None:
    if value is None:
        return None
    return _validate_nonnegative_decimal(value, location)


def _validate_nonnegative_decimal(value: object, location: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, float)):
        _invalid(f"{location}必须是非负数")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        _invalid(f"{location}必须是非负数")
    if not decimal_value.is_finite() or decimal_value < 0:
        _invalid(f"{location}必须是非负数")
    if decimal_value.as_tuple().exponent < -2:
        _invalid(f"{location}最多保留两位小数")
    return decimal_value


def _validate_optional_string(value: object, location: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        _invalid(f"{location}必须是非空字符串或null")
    return value


def _require_mapping(value: object, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _invalid(f"{location}必须是对象")
    return value


def _require_exact_fields(
    value: Mapping[str, Any], expected: tuple[str, ...], location: str
) -> None:
    if set(value) != set(expected):
        _invalid(f"{location}字段不完整或包含未知字段")


def _invalid(message: str) -> NoReturn:
    raise MenuPlanningError(400, message)


__all__ = ["validate_menu_planning_input"]
