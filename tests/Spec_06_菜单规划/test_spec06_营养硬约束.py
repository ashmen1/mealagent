from __future__ import annotations

from decimal import Decimal

import pytest

from .spec06_support import (
    build_candidate,
    build_dish,
    build_nutrition,
    build_planning_input,
)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sodium_mg", "800.01"),
        ("calcium_mg", "800.01"),
        ("iron_mg", "16.81"),
    ],
)
def test_正常人超过PI或UL只评为bad不判无解(field, value, invoke_plan):
    candidate = build_candidate(
        nutrition=build_nutrition(**{field: Decimal(value)})
    )
    planning_input = build_planning_input(
        dishes=[build_dish(candidates=[candidate])]
    )

    result = invoke_plan(planning_input)

    assert result["nutrient_grades"][field]["grade"] == "bad"


def test_正常人多人目标仍只用于评分(invoke_plan):
    candidate = build_candidate(
        nutrition=build_nutrition(
            energy_kcal="1600",
            protein_g="60",
            fat_g="50",
            carbohydrate_g="230",
            fiber_g="22",
            sodium_mg="1600",
            calcium_mg="1600",
            iron_mg="33.60",
        )
    )
    planning_input = build_planning_input(
        diner_count=2,
        dishes=[build_dish(count=1, candidates=[candidate])],
    )
    result = invoke_plan(planning_input)
    assert result["total_nutrition"]["sodium_mg"] == Decimal("1600")


def test_高血压启用钠硬约束并记录已应用规则(invoke_plan):
    planning_input = build_planning_input(special_populations=["高血压"])
    result = invoke_plan(planning_input)
    assert result["applied_health_constraints"] == ["高血压"]
    assert result["total_nutrition"]["sodium_mg"] <= Decimal("800")


def test_高血压超过钠上限返回422(assert_plan_error):
    candidate = build_candidate(
        nutrition=build_nutrition(sodium_mg="800.01")
    )
    planning_input = build_planning_input(
        special_populations=["高血压"],
        dishes=[build_dish(candidates=[candidate])],
    )

    assert_plan_error(planning_input, expected_status=422)


def test_高血压不把钙铁上限升级为硬约束(invoke_plan):
    candidate = build_candidate(
        nutrition=build_nutrition(calcium_mg="800.01", iron_mg="16.81")
    )
    planning_input = build_planning_input(
        special_populations=["高血压"],
        dishes=[build_dish(candidates=[candidate])],
    )

    result = invoke_plan(planning_input)

    assert result["applied_health_constraints"] == ["高血压"]
    assert result["nutrient_grades"]["calcium_mg"]["grade"] == "bad"
    assert result["nutrient_grades"]["iron_mg"]["grade"] == "bad"


def build_high_glucose_candidate(
    *,
    protein_g: str = "30",
    fat_g: str = "20",
    carbohydrate_g: str = "100",
) -> dict:
    return build_candidate(
        nutrition=build_nutrition(
            energy_kcal="800",
            protein_g=protein_g,
            fat_g=fat_g,
            carbohydrate_g=carbohydrate_g,
        )
    )


def test_高血糖三类供能比边界可行(invoke_plan):
    candidate = build_high_glucose_candidate(
        protein_g="30",
        fat_g="17.78",
        carbohydrate_g="90",
    )
    planning_input = build_planning_input(
        special_populations=["高血糖"],
        dishes=[build_dish(candidates=[candidate])],
    )
    result = invoke_plan(planning_input)
    assert result["applied_health_constraints"] == ["高血糖"]


@pytest.mark.parametrize(
    ("overrides"),
    [
        {"protein_g": "29.99"},
        {"protein_g": "40.01"},
        {"fat_g": "17.77"},
        {"fat_g": "31.12"},
        {"carbohydrate_g": "89.99"},
        {"carbohydrate_g": "120.01"},
    ],
)
def test_高血糖任一供能比越界即无解(overrides, assert_plan_error):
    candidate = build_high_glucose_candidate(**overrides)
    planning_input = build_planning_input(
        special_populations=["高血糖"],
        dishes=[build_dish(candidates=[candidate])],
    )
    assert_plan_error(planning_input, expected_status=422)


def test_多种特殊人群硬约束取交集(invoke_plan):
    candidate = build_high_glucose_candidate()
    planning_input = build_planning_input(
        special_populations=["高血压", "高血糖"],
        dishes=[build_dish(candidates=[candidate])],
    )
    result = invoke_plan(planning_input)
    assert result["applied_health_constraints"] == ["高血压", "高血糖"]


def test_高尿酸和备孕只披露未应用规则(invoke_plan):
    planning_input = build_planning_input(
        special_populations=["高尿酸", "备孕"]
    )
    result = invoke_plan(planning_input)
    assert result["unapplied_health_constraints"] == ["高尿酸", "备孕"]
