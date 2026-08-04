from __future__ import annotations

from decimal import Decimal

from spec01_support import invoke_extract, production_contract, profile_factory


def test_从健康档案提取完整统一约束(profile_factory, invoke_extract):
    profile = profile_factory(
        id=25,
        sex="女",
        age=49,
        activity_level="高",
        special_populations=["孕妇", "高血压"],
        gestational_week=12,
        taste_preference="酸甜",
        allergens=["花生", "牛奶"],
        health_goals=["控制体重"],
        height_cm=Decimal("180"),
        weight_kg=Decimal("75"),
        bmi=Decimal("23.1"),
        medical_metrics={"空腹血糖": 5.2},
    )

    result = invoke_extract(profile)

    assert result == {
        "profile_id": 25,
        "special_populations": ["孕妇", "高血压"],
        "taste_preferences": {
            "is_sour": True,
            "is_sweet": True,
        },
        "allergens": ["花生", "牛奶"],
    }
