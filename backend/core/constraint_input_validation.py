from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from typing import Any, NoReturn

from backend.core.constraint_integration_contract import (
    ConstraintIntegrationValidationError,
)
from backend.core.dialogue_constraint_contract import (
    CUISINES,
    DISH_TYPES,
    EFFECTS,
    INGREDIENT_CONCEPTS,
    INGREDIENT_REQUIREMENT_FIELDS,
    INGREDIENT_REQUIREMENT_KINDS,
    MEAL_PERIODS,
    SPECIAL_POPULATIONS,
    TASTE_PREFERENCES,
)
from backend.core.profile_constraint_contract import (
    VALID_ALLERGENS,
    VALID_PROFILE_ID_MAX,
    VALID_PROFILE_ID_MIN,
    VALID_SPECIAL_POPULATIONS,
)


PROFILE_FIELDS = (
    "profile_id",
    "special_populations",
    "taste_preferences",
    "allergens",
)
# Spec_03 尚未迁移到统一对话输出，这里仅保留其原有输入边界。
LEGACY_SINGLE_TURN_DIALOGUE_FIELDS = frozenset(
    (
        "dialogue_id",
        "meal_periods",
        "diner_count",
        "max_total_time_minutes",
        "available_ingredients",
        "dishes",
        "evidence",
    )
)
LEGACY_MULTI_TURN_DIALOGUE_FIELDS = frozenset(
    (
        "dialogue_id",
        "meal_periods",
        "diner_count",
        "total_dish_count",
        "max_total_time_minutes",
        "max_difficulty",
        "available_ingredients",
        "dishes",
        "evidence",
    )
)
LEGACY_INTEGRATION_DISH_FIELDS = (
    "count",
    "dish_type",
    "taste_preferences",
    "cuisines",
    "effects",
    "special_populations",
    "required_ingredients",
)


def validate_integration_inputs(
    profile_constraints: object,
    dialogue_constraints: object,
) -> None:
    """确认档案与单轮或多轮对话约束符合各自输出结构。"""

    _validate_profile_constraints(profile_constraints)
    _validate_dialogue_constraints(dialogue_constraints)


def _validate_profile_constraints(value: object) -> None:
    profile = _require_mapping(value, "profile_constraints")
    _require_exact_fields(profile, PROFILE_FIELDS, "profile_constraints")

    profile_id = profile["profile_id"]
    if type(profile_id) is not int or not (
        VALID_PROFILE_ID_MIN <= profile_id <= VALID_PROFILE_ID_MAX
    ):
        _invalid("profile_constraints.profile_id不合法")

    _validate_string_array(
        profile["special_populations"],
        "profile_constraints.special_populations",
        VALID_SPECIAL_POPULATIONS,
    )
    _validate_taste_preferences(
        profile["taste_preferences"],
        "profile_constraints.taste_preferences",
    )
    _validate_string_array(
        profile["allergens"],
        "profile_constraints.allergens",
        VALID_ALLERGENS,
    )


def _validate_dialogue_constraints(value: object) -> None:
    dialogue = _require_mapping(value, "dialogue_constraints")
    actual_fields = frozenset(dialogue)
    if actual_fields == LEGACY_SINGLE_TURN_DIALOGUE_FIELDS:
        is_multi_turn = False
    elif actual_fields == LEGACY_MULTI_TURN_DIALOGUE_FIELDS:
        is_multi_turn = True
    else:
        _invalid("dialogue_constraints字段不符合对应Spec")

    _validate_positive_integer(
        dialogue["dialogue_id"],
        "dialogue_constraints.dialogue_id",
    )
    _validate_string_array(
        dialogue["meal_periods"],
        "dialogue_constraints.meal_periods",
        MEAL_PERIODS,
    )
    _validate_optional_positive_integer(
        dialogue["diner_count"],
        "dialogue_constraints.diner_count",
    )
    if is_multi_turn:
        _validate_optional_positive_integer(
            dialogue["total_dish_count"],
            "dialogue_constraints.total_dish_count",
        )
    _validate_optional_positive_integer(
        dialogue["max_total_time_minutes"],
        "dialogue_constraints.max_total_time_minutes",
    )
    if is_multi_turn and dialogue["max_difficulty"] not in {
        None,
        "简单",
        "中等",
    }:
        _invalid(
            "dialogue_constraints.max_difficulty只允许简单、中等或null"
        )
    _validate_string_array(
        dialogue["available_ingredients"],
        "dialogue_constraints.available_ingredients",
    )
    _validate_dishes(dialogue["dishes"])
    _validate_evidence(dialogue, is_multi_turn=is_multi_turn)


def _validate_dishes(value: object) -> None:
    if not isinstance(value, list) or not value:
        _invalid("dialogue_constraints.dishes必须是非空数组")
    _require_no_duplicates(value, "dialogue_constraints.dishes")
    for dish_index, dish in enumerate(value):
        _validate_dish(dish, dish_index)


def _validate_dish(value: object, dish_index: int) -> None:
    location = f"dialogue_constraints.dishes[{dish_index}]"
    dish = _require_mapping(value, location)
    _require_exact_fields(dish, LEGACY_INTEGRATION_DISH_FIELDS, location)

    _validate_optional_positive_integer(dish["count"], f"{location}.count")
    if not isinstance(dish["dish_type"], str):
        _invalid(f"{location}.dish_type必须是字符串")
    if dish["dish_type"] not in DISH_TYPES:
        _invalid(f"{location}.dish_type不在允许值中")
    _validate_taste_preferences(
        dish["taste_preferences"],
        f"{location}.taste_preferences",
    )

    for field, allowed_values in (
        ("cuisines", CUISINES),
        ("effects", EFFECTS),
        ("special_populations", SPECIAL_POPULATIONS),
    ):
        _validate_string_array(
            dish[field],
            f"{location}.{field}",
            allowed_values,
        )
    _validate_ingredient_requirements(dish["required_ingredients"], location)


def _validate_ingredient_requirements(value: object, dish_location: str) -> None:
    location = f"{dish_location}.required_ingredients"
    if not isinstance(value, list):
        _invalid(f"{location}必须是数组")
    _require_no_duplicates(value, location)
    for requirement_index, requirement in enumerate(value):
        _validate_ingredient_requirement(
            requirement,
            f"{location}[{requirement_index}]",
        )


def _validate_ingredient_requirement(value: object, location: str) -> None:
    requirement = _require_mapping(value, location)
    _require_exact_fields(
        requirement,
        INGREDIENT_REQUIREMENT_FIELDS,
        location,
    )
    kind = requirement["kind"]
    ingredient_value = requirement["value"]
    if kind not in INGREDIENT_REQUIREMENT_KINDS:
        _invalid(f"{location}.kind不在允许值中")
    if not isinstance(ingredient_value, str) or not ingredient_value.strip():
        _invalid(f"{location}.value必须是非空字符串")
    if kind == "concept" and ingredient_value not in INGREDIENT_CONCEPTS:
        _invalid(f"{location}.value不在概念允许值中")


def _validate_evidence(
    dialogue: Mapping[str, Any],
    *,
    is_multi_turn: bool,
) -> None:
    evidence = _require_mapping(
        dialogue["evidence"],
        "dialogue_constraints.evidence",
    )
    if any(
        not isinstance(path, str)
        or not isinstance(fragment, str)
        or not fragment.strip()
        for path, fragment in evidence.items()
    ):
        _invalid("dialogue_constraints.evidence必须包含非空字符串键值")
    if set(evidence) != _collect_evidence_paths(
        dialogue,
        is_multi_turn=is_multi_turn,
    ):
        _invalid("dialogue_constraints.evidence路径不完整")


def _validate_taste_preferences(value: object, location: str) -> None:
    tastes = _require_mapping(value, location)
    if any(key not in TASTE_PREFERENCES for key in tastes):
        _invalid(f"{location}包含非法键")
    if any(type(enabled) is not bool for enabled in tastes.values()):
        _invalid(f"{location}的值必须是布尔值")


def _validate_string_array(
    value: object,
    location: str,
    allowed_values: Collection[str] | None = None,
) -> None:
    if not isinstance(value, list):
        _invalid(f"{location}必须是数组")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        _invalid(f"{location}的元素必须是非空字符串")
    _require_no_duplicates(value, location)
    if allowed_values is not None and any(
        item not in allowed_values for item in value
    ):
        _invalid(f"{location}包含非法值")


def _validate_positive_integer(value: object, location: str) -> None:
    if type(value) is not int or value <= 0:
        _invalid(f"{location}必须是正整数")


def _validate_optional_positive_integer(value: object, location: str) -> None:
    if value is not None:
        _validate_positive_integer(value, location)


def _require_mapping(value: object, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _invalid(f"{location}必须是对象")
    return value


def _require_exact_fields(
    value: Mapping[str, Any],
    fields: Collection[str],
    location: str,
) -> None:
    if set(value) != set(fields):
        _invalid(f"{location}字段不符合对应Spec")


def _require_no_duplicates(values: list[Any], location: str) -> None:
    try:
        canonical_values = [
            json.dumps(value, ensure_ascii=False, sort_keys=True)
            for value in values
        ]
    except (TypeError, ValueError):
        _invalid(f"{location}包含非法值")
    if len(canonical_values) != len(set(canonical_values)):
        _invalid(f"{location}不允许重复值")


def _collect_evidence_paths(
    dialogue: Mapping[str, Any],
    *,
    is_multi_turn: bool,
) -> set[str]:
    paths = {
        f"meal_periods[{index}]"
        for index in range(len(dialogue["meal_periods"]))
    }
    if dialogue["diner_count"] is not None:
        paths.add("diner_count")
    if is_multi_turn and dialogue["total_dish_count"] is not None:
        paths.add("total_dish_count")
    if dialogue["max_total_time_minutes"] is not None:
        paths.add("max_total_time_minutes")
    if is_multi_turn and dialogue["max_difficulty"] is not None:
        paths.add("max_difficulty")
    paths.update(
        f"available_ingredients[{index}]"
        for index in range(len(dialogue["available_ingredients"]))
    )
    for dish_index, dish in enumerate(dialogue["dishes"]):
        paths.update(_collect_dish_evidence_paths(dish, dish_index))
    return paths


def _collect_dish_evidence_paths(
    dish: Mapping[str, Any],
    dish_index: int,
) -> set[str]:
    prefix = f"dishes[{dish_index}]"
    paths: set[str] = set()
    if dish["count"] is not None:
        paths.add(f"{prefix}.count")
    if dish["dish_type"] != "未指定":
        paths.add(f"{prefix}.dish_type")
    paths.update(
        f"{prefix}.taste_preferences.{taste}"
        for taste in dish["taste_preferences"]
    )
    for field in ("cuisines", "effects", "special_populations"):
        paths.update(
            f"{prefix}.{field}[{index}]"
            for index in range(len(dish[field]))
        )
    paths.update(
        f"{prefix}.required_ingredients[{index}].value"
        for index in range(len(dish["required_ingredients"]))
    )
    return paths


def _invalid(message: str) -> NoReturn:
    raise ConstraintIntegrationValidationError(message)


__all__ = ["validate_integration_inputs"]
