from __future__ import annotations

from typing import NoReturn

from backend.core.constraint_input_validation import (
    _require_exact_fields,
    _require_mapping,
    _require_no_duplicates,
    _validate_optional_positive_integer,
    _validate_positive_integer,
    _validate_string_array,
    _validate_taste_preferences,
)
from backend.core.dish_filtering_contract import (
    DishFilteringValidationError,
    INTEGRATED_DISH_FIELDS,
    INTEGRATED_TOP_LEVEL_FIELDS,
    INGREDIENT_REQUIREMENT_FIELDS,
)
from backend.core.dialogue_constraint_contract import (
    CUISINES,
    DISH_TYPES,
    EFFECTS,
    INGREDIENT_CONCEPTS,
    INGREDIENT_REQUIREMENT_KINDS,
    MEAL_PERIODS,
)


def validate_integrated_constraints(constraints: object) -> None:
    """确认输入符合 Spec_04 的 IntegratedConstraints 结构。"""
    _require_mapping(constraints, "constraints")
    _require_exact_fields(
        constraints, INTEGRATED_TOP_LEVEL_FIELDS, "constraints"
    )
    _validate_positive_integer(
        constraints["profile_id"], "constraints.profile_id"
    )
    _validate_positive_integer(
        constraints["dialogue_id"], "constraints.dialogue_id"
    )
    _validate_string_array(
        constraints["meal_periods"],
        "constraints.meal_periods",
        MEAL_PERIODS,
    )
    _validate_optional_positive_integer(
        constraints["diner_count"], "constraints.diner_count"
    )
    _validate_optional_positive_integer(
        constraints["max_total_time_minutes"],
        "constraints.max_total_time_minutes",
    )
    _validate_string_array(
        constraints["available_ingredients"],
        "constraints.available_ingredients",
    )
    _validate_string_array(
        constraints["allergens"], "constraints.allergens"
    )
    _validate_dishes(constraints["dishes"])
    if type(constraints["has_conflicts"]) is not bool:
        _invalid("constraints.has_conflicts必须是布尔值")
    if not isinstance(constraints["conflicts"], list):
        _invalid("constraints.conflicts必须是数组")
    if constraints["has_conflicts"] != bool(constraints["conflicts"]):
        _invalid("constraints.has_conflicts必须等于conflicts是否非空")
    _validate_conflicts(constraints)


def _validate_conflicts(constraints: object) -> None:
    conflicts = constraints["conflicts"]
    _require_no_duplicates(conflicts, "constraints.conflicts")
    profile_references = {
        f"allergens[{index}]": allergen
        for index, allergen in enumerate(constraints["allergens"])
    }
    dialogue_references = {
        f"dishes[{dish_index}].required_ingredients[{requirement_index}].value": (
            dish_index,
            requirement,
        )
        for dish_index, dish in enumerate(constraints["dishes"])
        for requirement_index, requirement in enumerate(
            dish["required_ingredients"]
        )
    }
    conflict_fields = (
        "code",
        "dish_index",
        "profile_path",
        "dialogue_path",
        "allergen",
        "required_ingredient",
        "dialogue_evidence",
    )
    for conflict_index, value in enumerate(conflicts):
        location = f"constraints.conflicts[{conflict_index}]"
        conflict = _require_mapping(value, location)
        _require_exact_fields(conflict, conflict_fields, location)
        if conflict["code"] != "allergen_required_ingredient":
            _invalid(f"{location}.code不在允许值中")
        if conflict["profile_path"] not in profile_references:
            _invalid(f"{location}.profile_path无效")
        if conflict["dialogue_path"] not in dialogue_references:
            _invalid(f"{location}.dialogue_path无效")
        dish_index, requirement = dialogue_references[
            conflict["dialogue_path"]
        ]
        if type(conflict["dish_index"]) is not int or (
            conflict["dish_index"] != dish_index
        ):
            _invalid(f"{location}.dish_index无效")
        if conflict["allergen"] != profile_references[
            conflict["profile_path"]
        ]:
            _invalid(f"{location}.allergen与profile_path不一致")
        _validate_ingredient_requirement(
            conflict["required_ingredient"],
            f"{location}.required_ingredient",
        )
        if conflict["required_ingredient"] != requirement:
            _invalid(
                f"{location}.required_ingredient与dialogue_path不一致"
            )
        if (
            not isinstance(conflict["dialogue_evidence"], str)
            or not conflict["dialogue_evidence"].strip()
        ):
            _invalid(f"{location}.dialogue_evidence必须是非空字符串")


def _validate_dishes(value: object) -> None:
    if not isinstance(value, list) or not value:
        _invalid("constraints.dishes必须是非空数组")
    _require_no_duplicates(value, "constraints.dishes")
    for dish_index, dish in enumerate(value):
        _validate_dish(dish, dish_index)


def _validate_dish(value: object, dish_index: int) -> None:
    location = f"constraints.dishes[{dish_index}]"
    dish = _require_mapping(value, location)
    _require_exact_fields(dish, INTEGRATED_DISH_FIELDS, location)

    _validate_optional_positive_integer(dish["count"], f"{location}.count")
    if dish["dish_type"] not in DISH_TYPES:
        _invalid(f"{location}.dish_type不在允许值中")
    _validate_taste_preferences(
        dish["taste_preferences"], f"{location}.taste_preferences"
    )
    # 菜系/功效用 spec_02 枚举；人群可含档案值（孕妇等无标签对应，
    # 由过滤阶段忽略），只做类型检查
    for field, allowed_values in (
        ("cuisines", CUISINES),
        ("effects", EFFECTS),
    ):
        _validate_string_array(
            dish[field], f"{location}.{field}", allowed_values
        )
    _validate_string_array(
        dish["special_populations"], f"{location}.special_populations"
    )
    _validate_ingredient_requirements(
        dish["required_ingredients"], location
    )


def _validate_ingredient_requirements(value: object, dish_location: str) -> None:
    location = f"{dish_location}.required_ingredients"
    if not isinstance(value, list):
        _invalid(f"{location}必须是数组")
    _require_no_duplicates(value, location)
    for requirement_index, requirement in enumerate(value):
        _validate_ingredient_requirement(
            requirement, f"{location}[{requirement_index}]"
        )


def _validate_ingredient_requirement(value: object, location: str) -> None:
    requirement = _require_mapping(value, location)
    _require_exact_fields(
        requirement, INGREDIENT_REQUIREMENT_FIELDS, location
    )
    kind = requirement["kind"]
    ingredient_value = requirement["value"]
    if kind not in INGREDIENT_REQUIREMENT_KINDS:
        _invalid(f"{location}.kind不在允许值中")
    if not isinstance(ingredient_value, str) or not ingredient_value.strip():
        _invalid(f"{location}.value必须是非空字符串")
    if kind == "concept" and ingredient_value not in INGREDIENT_CONCEPTS:
        _invalid(f"{location}.value不在概念允许值中")


def _invalid(message: str) -> NoReturn:
    raise DishFilteringValidationError(400, message)


__all__ = ["validate_integrated_constraints"]
