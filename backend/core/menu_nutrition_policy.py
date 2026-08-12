from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from backend.core.menu_planning_contract import (
    MenuPlanningError,
    NutrientGrade,
    NutritionValues,
)


@dataclass(frozen=True)
class NutrientGradeBand:
    """一项营养的普通与优秀区间，空边界表示不限。"""

    normal_lower: Decimal | None
    normal_upper: Decimal | None
    excellent_lower: Decimal | None
    excellent_upper: Decimal | None


def build_nutrient_grade_bands(
    targets: Mapping[str, Mapping[str, Any]],
    diners: int,
) -> dict[str, NutrientGradeBand]:
    """将单人营养目标转换为整桌八项评分区间。"""

    multiplier = Decimal(diners)
    energy_target = (
        _required_decimal(targets, "energy_kcal", "target_value")
        * multiplier
    )
    protein_lower = max(
        _required_decimal(targets, "protein_g", "target_value"),
        _required_decimal(targets, "protein_g", "lower_bound"),
    ) * multiplier
    protein_upper = (
        _required_decimal(targets, "protein_g", "upper_bound")
        * multiplier
    )

    bands = {
        "energy_kcal": NutrientGradeBand(
            normal_lower=energy_target * Decimal("0.80"),
            normal_upper=energy_target * Decimal("1.20"),
            excellent_lower=energy_target * Decimal("0.90"),
            excellent_upper=energy_target * Decimal("1.10"),
        ),
        "protein_g": NutrientGradeBand(
            normal_lower=protein_lower * Decimal("0.80"),
            normal_upper=protein_upper * Decimal("1.20"),
            excellent_lower=protein_lower,
            excellent_upper=protein_upper,
        ),
    }
    for nutrient in ("fat_g", "carbohydrate_g", "fiber_g"):
        excellent_lower = (
            _required_decimal(targets, nutrient, "lower_bound")
            * multiplier
        )
        excellent_upper = (
            _required_decimal(targets, nutrient, "upper_bound")
            * multiplier
        )
        bands[nutrient] = NutrientGradeBand(
            normal_lower=excellent_lower * Decimal("0.80"),
            normal_upper=excellent_upper * Decimal("1.20"),
            excellent_lower=excellent_lower,
            excellent_upper=excellent_upper,
        )

    sodium_ai = (
        _required_decimal(targets, "sodium_mg", "target_value")
        * multiplier
    )
    sodium_pi = (
        _required_decimal(targets, "sodium_mg", "upper_bound")
        * multiplier
    )
    bands["sodium_mg"] = NutrientGradeBand(
        normal_lower=None,
        normal_upper=sodium_pi,
        excellent_lower=None,
        excellent_upper=sodium_ai,
    )

    for nutrient in ("calcium_mg", "iron_mg"):
        rni = (
            _required_decimal(targets, nutrient, "target_value")
            * multiplier
        )
        upper = (
            _required_decimal(targets, nutrient, "upper_bound")
            * multiplier
        )
        bands[nutrient] = NutrientGradeBand(
            normal_lower=rni * Decimal("0.80"),
            normal_upper=upper,
            excellent_lower=rni,
            excellent_upper=upper,
        )
    return bands


def grade_nutrients(
    totals: NutritionValues,
    targets: Mapping[str, Mapping[str, Any]],
    diners: int,
) -> dict[str, NutrientGrade]:
    """按统一区间评定八项营养，胆固醇只展示。"""

    bands = build_nutrient_grade_bands(targets, diners)
    grades = {
        nutrient: _grade_value(totals[nutrient], band)
        for nutrient, band in bands.items()
    }
    grades["cholesterol_mg"] = {
        "status": "not_established",
        "actual_value": totals["cholesterol_mg"],
        "grade": None,
        "score": None,
    }
    return grades


def _grade_value(
    actual: Decimal,
    band: NutrientGradeBand,
) -> NutrientGrade:
    if _is_within(actual, band.excellent_lower, band.excellent_upper):
        grade, score = "excellent", 2
    elif _is_within(actual, band.normal_lower, band.normal_upper):
        grade, score = "normal", 1
    else:
        grade, score = "bad", 0
    return {
        "status": "available",
        "actual_value": actual,
        "grade": grade,
        "score": score,
    }


def _is_within(
    value: Decimal,
    lower: Decimal | None,
    upper: Decimal | None,
) -> bool:
    return (lower is None or value >= lower) and (
        upper is None or value <= upper
    )


def _required_decimal(
    targets: Mapping[str, Mapping[str, Any]],
    nutrient: str,
    field: str,
) -> Decimal:
    value = targets[nutrient][field]
    if not isinstance(value, Decimal):
        raise MenuPlanningError(400, f"{nutrient}.{field}必填")
    return value


__all__ = [
    "NutrientGradeBand",
    "build_nutrient_grade_bands",
    "grade_nutrients",
]
