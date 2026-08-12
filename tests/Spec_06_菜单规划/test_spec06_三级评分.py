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
    ("field", "normal_value", "bad_value"),
    [
        ("energy_kcal", "680.00", "630.00"),
        ("protein_g", "22.00", "19.00"),
        ("fat_g", "16.00", "14.00"),
        ("carbohydrate_g", "90.00", "79.00"),
        ("fiber_g", "9.00", "7.00"),
        ("calcium_mg", "300.00", "250.00"),
        ("iron_mg", "4.00", "3.00"),
    ],
)
def test_七项范围营养分别支持normal和bad等级(
    field,
    normal_value,
    bad_value,
    invoke_plan,
):
    normal_input = build_planning_input(
        dishes=[
            build_dish(
                candidates=[
                    build_candidate(
                        nutrition=build_nutrition(
                            **{field: Decimal(normal_value)}
                        )
                    )
                ]
            )
        ]
    )
    bad_input = build_planning_input(
        dishes=[
            build_dish(
                candidates=[
                    build_candidate(
                        nutrition=build_nutrition(
                            **{field: Decimal(bad_value)}
                        )
                    )
                ]
            )
        ]
    )

    normal_result = invoke_plan(normal_input)
    bad_result = invoke_plan(bad_input)

    assert normal_result["nutrient_grades"][field]["grade"] == "normal"
    assert normal_result["nutrient_grades"][field]["score"] == 1
    assert normal_result["nutrition_score"] == 15
    assert bad_result["nutrient_grades"][field]["grade"] == "bad"
    assert bad_result["nutrient_grades"][field]["score"] == 0
    assert bad_result["nutrition_score"] == 14


def test_钠按AI与PI划分优秀和正常(invoke_plan):
    excellent = invoke_plan(
        build_planning_input(
            dishes=[
                build_dish(
                    candidates=[
                        build_candidate(
                            nutrition=build_nutrition(sodium_mg="600")
                        )
                    ]
                )
            ]
        )
    )
    normal = invoke_plan(
        build_planning_input(
            dishes=[
                build_dish(
                    candidates=[
                        build_candidate(
                            nutrition=build_nutrition(sodium_mg="700")
                        )
                    ]
                )
            ]
        )
    )
    assert excellent["nutrient_grades"]["sodium_mg"]["grade"] == "excellent"
    assert normal["nutrient_grades"]["sodium_mg"]["grade"] == "normal"


def test_胆固醇只展示且不评分(invoke_plan):
    result = invoke_plan(build_planning_input())
    cholesterol = result["nutrient_grades"]["cholesterol_mg"]
    assert cholesterol["status"] == "not_established"
    assert cholesterol["grade"] is None
    assert cholesterol["score"] is None
    assert result["nutrition_score"] == 16
