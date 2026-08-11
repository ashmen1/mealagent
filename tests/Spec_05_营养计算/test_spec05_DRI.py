from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from spec05_support import default_dri_rule, default_profile


@pytest.mark.parametrize(
    ("profile", "dri_rule", "expected_energy", "expected_protein", "expected_iron"),
    [
        (
            default_profile(年龄=18),
            default_dri_rule(年龄下限=18, 年龄上限=29, energy_mj="9.00"),
            "645.30",
            "19.50",
            "3.60",
        ),
        (
            default_profile(年龄=50),
            default_dri_rule(年龄下限=50, 年龄上限=64, energy_mj="8.16"),
            "585.07",
            "19.50",
            "3.60",
        ),
        (
            default_profile(年龄=65),
            default_dri_rule(
                年龄下限=65,
                年龄上限=74,
                energy_mj="7.95",
                protein_rni_g="72",
                protein_amdr_min_percent="15",
            ),
            "570.02",
            "21.60",
            "3.60",
        ),
        (
            default_profile(年龄=75),
            default_dri_rule(
                年龄下限=75,
                年龄上限=200,
                energy_mj="7.53",
                protein_rni_g="72",
                protein_amdr_min_percent="15",
            ),
            "539.90",
            "21.60",
            "3.60",
        ),
        (
            default_profile(
                性别="女", 年龄=30, 特殊人群=["孕妇"], 孕周期="12周"
            ),
            default_dri_rule(
                性别="女",
                年龄下限=18,
                年龄上限=49,
                生理阶段="孕早期",
                energy_mj="7.11",
                protein_rni_g="55",
                iron_rni_mg="18",
            ),
            "509.79",
            "16.50",
            "5.40",
        ),
        (
            default_profile(
                性别="女", 年龄=30, 特殊人群=["孕妇"], 孕周期="13周"
            ),
            default_dri_rule(
                性别="女",
                年龄下限=18,
                年龄上限=49,
                生理阶段="孕中期",
                energy_mj="8.16",
                protein_rni_g="70",
                iron_rni_mg="25",
                fiber_ai_min_g="29",
                fiber_ai_max_g="34",
            ),
            "585.07",
            "21.00",
            "7.50",
        ),
        (
            default_profile(
                性别="女", 年龄=30, 特殊人群=["孕妇"], 孕周期="28周"
            ),
            default_dri_rule(
                性别="女",
                年龄下限=18,
                年龄上限=49,
                生理阶段="孕晚期",
                energy_mj="8.78",
                protein_rni_g="85",
                iron_rni_mg="29",
                fiber_ai_min_g="29",
                fiber_ai_max_g="34",
            ),
            "629.53",
            "25.50",
            "8.70",
        ),
        (
            default_profile(性别="女", 年龄=30, 特殊人群=["哺乳期"]),
            default_dri_rule(
                性别="女",
                年龄下限=18,
                年龄上限=49,
                生理阶段="哺乳期",
                energy_mj="8.78",
                protein_rni_g="80",
                iron_rni_mg="24",
                fiber_ai_min_g="29",
                fiber_ai_max_g="34",
            ),
            "629.53",
            "24.00",
            "7.20",
        ),
    ],
)
def test_不同成人与生理阶段命中对应早餐DRI(
    profile,
    dri_rule,
    expected_energy,
    expected_protein,
    expected_iron,
    input_factory,
    invoke_import,
    import_contract,
    db_session,
):
    paths = input_factory.create(profiles=[profile], dri_rules=[dri_rule])

    invoke_import(paths, db_session)

    rows = list(
        db_session.scalars(
            select(import_contract.ProfileDriTarget).where(
                import_contract.ProfileDriTarget.meal_period == "早餐"
            )
        )
    )
    by_nutrient = {row.nutrient: row for row in rows}
    assert by_nutrient["energy_kcal"].target_value == Decimal(expected_energy)
    assert by_nutrient["protein_g"].target_value == Decimal(expected_protein)
    assert by_nutrient["iron_mg"].target_value == Decimal(expected_iron)


def test_女性50至64岁铁RNI按月经状态区分(
    input_factory,
    invoke_import,
    import_contract,
    db_session,
):
    profiles = [
        default_profile(id=51, 性别="女", 年龄=50, 是否有月经=True),
        default_profile(id=52, 性别="女", 年龄=50, 是否有月经=False),
    ]
    rules = [
        default_dri_rule(
            性别="女",
            年龄下限=50,
            年龄上限=64,
            energy_mj="6.69",
            protein_rni_g="55",
            iron_rni_mg="18",
        ),
        default_dri_rule(
            性别="女",
            年龄下限=50,
            年龄上限=64,
            生理阶段="无月经",
            energy_mj="6.69",
            protein_rni_g="55",
            iron_rni_mg="10",
        ),
    ]
    paths = input_factory.create(profiles=profiles, dri_rules=rules)

    invoke_import(paths, db_session)

    rows = list(
        db_session.scalars(
            select(import_contract.ProfileDriTarget).where(
                import_contract.ProfileDriTarget.meal_period == "午餐",
                import_contract.ProfileDriTarget.nutrient == "iron_mg",
            )
        )
    )
    assert {row.profile_id: row.target_value for row in rows} == {
        51: Decimal("7.20"),
        52: Decimal("4.00"),
    }


def test_三餐分别按30_40_30生成且胆固醇未建立(
    input_factory,
    invoke_import,
    import_contract,
    db_session,
):
    paths = input_factory.create()

    invoke_import(paths, db_session)

    rows = list(db_session.scalars(select(import_contract.ProfileDriTarget)))
    energy = {
        row.meal_period: row.target_value
        for row in rows
        if row.nutrient == "energy_kcal"
    }
    assert energy == {
        "早餐": Decimal("615.19"),
        "午餐": Decimal("820.25"),
        "晚餐": Decimal("615.19"),
    }
    cholesterol = [row for row in rows if row.nutrient == "cholesterol_mg"]
    assert len(cholesterol) == 3
    assert all(row.status == "not_established" for row in cholesterol)
    assert all(
        row.target_value is None
        and row.lower_bound is None
        and row.upper_bound is None
        for row in cholesterol
    )
