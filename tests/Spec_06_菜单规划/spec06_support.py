from __future__ import annotations

import copy
import importlib
import sys
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest


SPEC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPEC_DIR.parents[1]
repo_root_text = str(REPO_ROOT)
if repo_root_text not in sys.path:
    sys.path.insert(0, repo_root_text)


NUTRIENT_FIELDS = (
    "energy_kcal",
    "protein_g",
    "fat_g",
    "carbohydrate_g",
    "fiber_g",
    "sodium_mg",
    "calcium_mg",
    "iron_mg",
    "cholesterol_mg",
)


def available_target(
    *,
    unit: str,
    target: str | None = None,
    lower: str | None = None,
    upper: str | None = None,
    target_basis: str | None = None,
    lower_basis: str | None = None,
    upper_basis: str | None = None,
) -> dict[str, Any]:
    return {
        "status": "available",
        "unit": unit,
        "target_value": Decimal(target) if target is not None else None,
        "lower_bound": Decimal(lower) if lower is not None else None,
        "upper_bound": Decimal(upper) if upper is not None else None,
        "target_basis": target_basis,
        "lower_basis": lower_basis,
        "upper_basis": upper_basis,
    }


def build_nutrient_targets() -> dict[str, dict[str, Any]]:
    return {
        "energy_kcal": available_target(
            unit="kcal", target="800.00", target_basis="EER"
        ),
        "protein_g": available_target(
            unit="g",
            target="25.00",
            lower="20.00",
            upper="40.00",
            target_basis="RNI",
            lower_basis="AMDR",
            upper_basis="AMDR",
        ),
        "fat_g": available_target(
            unit="g",
            lower="18.00",
            upper="30.00",
            lower_basis="AMDR",
            upper_basis="AMDR",
        ),
        "carbohydrate_g": available_target(
            unit="g",
            lower="100.00",
            upper="130.00",
            lower_basis="AMDR",
            upper_basis="AMDR",
        ),
        "fiber_g": available_target(
            unit="g",
            lower="10.00",
            upper="12.00",
            lower_basis="AI",
            upper_basis="AI",
        ),
        "sodium_mg": available_target(
            unit="mg",
            target="600.00",
            upper="800.00",
            target_basis="AI",
            upper_basis="PI",
        ),
        "calcium_mg": available_target(
            unit="mg",
            target="320.00",
            upper="800.00",
            target_basis="RNI",
            upper_basis="UL",
        ),
        "iron_mg": available_target(
            unit="mg",
            target="4.80",
            upper="16.80",
            target_basis="RNI",
            upper_basis="UL",
        ),
        "cholesterol_mg": {
            "status": "not_established",
            "unit": "mg",
            "target_value": None,
            "lower_bound": None,
            "upper_bound": None,
            "target_basis": None,
            "lower_basis": None,
            "upper_basis": None,
        },
    }


def build_nutrition(**overrides: Any) -> dict[str, Decimal]:
    nutrition = {
        "energy_kcal": Decimal("800.00"),
        "protein_g": Decimal("30.00"),
        "fat_g": Decimal("25.00"),
        "carbohydrate_g": Decimal("115.00"),
        "fiber_g": Decimal("11.00"),
        "sodium_mg": Decimal("600.00"),
        "calcium_mg": Decimal("400.00"),
        "iron_mg": Decimal("8.00"),
        "cholesterol_mg": Decimal("100.00"),
    }
    nutrition.update(
        {field: Decimal(str(value)) for field, value in overrides.items()}
    )
    return nutrition


def build_candidate(
    recipe_name: str = "标准午餐",
    *,
    recipe_type: str | None = "菜",
    matched_tags: list[str] | None = None,
    nutrition: dict[str, Decimal] | None = None,
) -> dict[str, Any]:
    return {
        "recipe_name": recipe_name,
        "recipe_type": recipe_type,
        "matched_tags": list(matched_tags or []),
        "nutrition": copy.deepcopy(nutrition or build_nutrition()),
    }


def build_dish(
    *,
    count: int | None = 1,
    dish_type: str = "菜",
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "count": count,
        "dish_type": dish_type,
        "candidates": copy.deepcopy(
            candidates if candidates is not None else [build_candidate()]
        ),
    }


def build_planning_input(
    *,
    profile_id: int = 25,
    dialogue_id: int = 1,
    meal_period: str = "午餐",
    diner_count: int | None = 1,
    total_dish_count: int | None = None,
    special_populations: list[str] | None = None,
    dishes: list[dict[str, Any]] | None = None,
    nutrient_targets: dict[str, dict[str, Any]] | None = None,
    unmatched_allergens: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "profile_id": profile_id,
        "dialogue_id": dialogue_id,
        "meal_period": meal_period,
        "diner_count": diner_count,
        "total_dish_count": total_dish_count,
        "special_populations": list(special_populations or []),
        "dishes": copy.deepcopy(
            dishes if dishes is not None else [build_dish()]
        ),
        "nutrient_targets": copy.deepcopy(
            nutrient_targets or build_nutrient_targets()
        ),
        "unmatched_allergens": list(unmatched_allergens or []),
    }


def candidates_with_split_nutrition(
    count: int,
    *,
    prefix: str = "菜谱",
) -> list[dict[str, Any]]:
    totals = build_nutrition()
    split_values: dict[str, list[Decimal]] = {}
    for field, total in totals.items():
        regular_share = (total / count).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )
        split_values[field] = [regular_share] * (count - 1) + [
            total - regular_share * (count - 1)
        ]

    return [
        build_candidate(
            f"{prefix}{index + 1}",
            nutrition={
                field: values[index]
                for field, values in split_values.items()
            },
        )
        for index in range(count)
    ]


@pytest.fixture
def production_contract():
    try:
        module = importlib.import_module("backend.services.menu_planning")
        return SimpleNamespace(
            MenuPlanningService=module.MenuPlanningService,
            MenuPlanningError=module.MenuPlanningError,
        )
    except (ModuleNotFoundError, AttributeError) as exc:
        pytest.fail(
            "缺少 Spec_06 约定的生产接口："
            "backend.services.menu_planning.MenuPlanningService 及其异常；"
            f"原始错误：{exc}",
            pytrace=False,
        )


@pytest.fixture
def invoke_plan(production_contract) -> Callable[..., dict[str, Any]]:
    def invoke(
        planning_input: dict[str, Any],
        *,
        solver_runner: Callable[..., Any] | None = None,
    ) -> dict[str, Any]:
        if solver_runner is None:
            service = production_contract.MenuPlanningService()
        else:
            service = production_contract.MenuPlanningService(
                solver_runner=solver_runner
            )
        return service.plan(planning_input)

    return invoke


@pytest.fixture
def assert_plan_error(invoke_plan):
    def assert_error(
        planning_input: dict[str, Any],
        expected_status: int,
        *,
        solver_runner: Callable[..., Any] | None = None,
    ) -> Exception:
        with pytest.raises(Exception) as captured:
            invoke_plan(planning_input, solver_runner=solver_runner)
        assert getattr(captured.value, "status_code", None) == expected_status
        return captured.value

    return assert_error
