from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from spec05_support import (
    default_ingredient,
    default_recipe,
    quantity_resolution,
    row_count,
)


@pytest.mark.parametrize(
    (
        "quantity_text",
        "expected_grams",
        "is_estimated",
    ),
    [
        ("100g", "100.00", False),
        ("125g", "125.00", False),
        ("1000g", "1000.00", False),
        ("1.5kg", "1500.00", False),
        ("10g;15g", "25.00", False),
        ("1个200g", "200.00", False),
        ("2根80g", "80.00", False),
        ("1个（50g）", "50.00", False),
        ("4条（120g/条）", "480.00", False),
        ("15—20g", "17.50", True),
        ("1kg-1.5kg", "1250.00", True),
        ("300~400g", "350.00", True),
        ("约5g", "5.00", True),
        ("10ml", "8.00", True),
        ("1大勺", "15.00", True),
        ("2个", "100.00", True),
        ("2方", "400.00", True),
    ],
)
def test_各类数量均转换为克重并正确标记估算(
    quantity_text,
    expected_grams,
    is_estimated,
    input_factory,
    invoke_import,
    import_contract,
    db_session,
):
    paths = input_factory.create(
        recipes=[
            default_recipe(
                quantity_text=quantity_text,
                resolved_grams=expected_grams,
                is_estimated=is_estimated,
            )
        ],
    )

    invoke_import(paths, db_session)

    association = db_session.scalar(select(import_contract.RecipeIngredient))
    assert association.resolved_quantity_g == Decimal(expected_grams)
    assert association.is_quantity_estimated is is_estimated


def test_菜谱九项营养按整份配方汇总并四舍五入两位(
    input_factory,
    invoke_import,
    import_contract,
    db_session,
):
    recipes = [
        {
            **default_recipe(),
            "ingredients": {"食材甲": "33g", "食材乙": "67g"},
            "ingredient_quantity_resolutions": {
                "食材甲": quantity_resolution("33g", 33),
                "食材乙": quantity_resolution("67g", 67),
            },
        }
    ]
    ingredients = [
        default_ingredient("食材甲", energy_kcal="100.555", iron_mg="0.555"),
        default_ingredient("食材乙", energy_kcal="200.555", iron_mg="1.555"),
    ]
    paths = input_factory.create(recipes=recipes, ingredients=ingredients)

    invoke_import(paths, db_session)

    nutrition = db_session.scalar(select(import_contract.RecipeNutrition))
    assert nutrition.energy_kcal == Decimal("167.56")
    assert nutrition.iron_mg == Decimal("1.23")
    for field in (
        "protein_g",
        "fat_g",
        "carbohydrate_g",
        "fiber_g",
        "sodium_mg",
        "calcium_mg",
        "cholesterol_mg",
    ):
        assert getattr(nutrition, field) >= 0


def test_模糊数量虽已改写为克数仍标记为估算(
    input_factory,
    invoke_import,
    import_contract,
    db_session,
):
    recipe = default_recipe(quantity_text="5g")
    recipe["fuzzy_quantity_estimates"] = [
        {
            "ingredient_canonical": "测试食材",
            "raw_quantity": "适量",
            "point_estimate_g": 5,
            "lower_bound_g": 2,
            "upper_bound_g": 8,
            "sample_count": 3,
            "source_status": "approved_internal_statistics",
            "estimation_method": "strict_mass_mode",
            "trace_reference": "Fuzzy_Quantifier_Estimation_Trace.json#测试食材",
            "resolved_quantity": "5g",
            "component_index": 0,
        }
    ]
    recipe["ingredient_quantity_resolutions"]["测试食材"] = quantity_resolution(
        "5g",
        5,
        is_estimated=True,
        calculation_path="原始适量 → 内部统计 → 5.00g",
        reference_source="Fuzzy_Quantifier_Estimation_Trace.json#测试食材",
    )
    paths = input_factory.create(recipes=[recipe])

    invoke_import(paths, db_session)

    association = db_session.scalar(select(import_contract.RecipeIngredient))
    assert association.resolved_quantity_g == Decimal("5.00")
    assert association.is_quantity_estimated is True


def test_明确质量与模糊分段分别解析后求和(
    input_factory,
    invoke_import,
    import_contract,
    db_session,
):
    recipe = default_recipe(
        quantity_text="40g; 10g; 适量",
        resolved_grams=53,
        is_estimated=True,
    )
    recipe["fuzzy_quantity_estimates"] = [
        {
            "ingredient_canonical": "测试食材",
            "raw_quantity": "40g; 10g; 适量",
            "point_estimate_g": 3,
            "lower_bound_g": 1,
            "upper_bound_g": 7,
            "sample_count": 30,
            "source_status": "approved_internal_statistics",
            "estimation_method": "nearest_rank_quantiles",
            "trace_reference": "trace#测试食材",
            "component_index": 2,
        }
    ]
    recipe["ingredient_quantity_resolutions"]["测试食材"] = quantity_resolution(
        "40g; 10g; 适量",
        53,
        is_estimated=True,
        calculation_path="40g+10g+适量3g → 分段求和53.00g",
        reference_source="trace#测试食材",
    )
    paths = input_factory.create(recipes=[recipe])

    invoke_import(paths, db_session)

    association = db_session.scalar(select(import_contract.RecipeIngredient))
    assert association.resolved_quantity_g == Decimal("53.00")
    assert association.is_quantity_estimated is True


@pytest.mark.parametrize("water_name", ["水", "清水", "温水", "温开水", "热水", "凉开水"])
def test_纯水保留关联但营养计算克重为零(
    water_name,
    input_factory,
    invoke_import,
    import_contract,
    db_session,
):
    recipe = default_recipe(quantity_text="适量", ingredient_name=water_name)
    paths = input_factory.create(
        recipes=[recipe],
        ingredients=[default_ingredient(water_name)],
    )

    invoke_import(paths, db_session)

    association = db_session.scalar(select(import_contract.RecipeIngredient))
    nutrition = db_session.scalar(select(import_contract.RecipeNutrition))
    assert association.resolved_quantity_g == Decimal("0.00")
    assert association.is_nutrition_excluded is True
    assert nutrition.energy_kcal == Decimal("0.00")


@pytest.mark.parametrize(
    ("quantity_text", "ingredient_overrides"),
    [
        ("一小撮", {}),
        ("10g", {"iron_mg": ""}),
    ],
)
def test_克重无法换算或营养缺失时整批回滚(
    quantity_text,
    ingredient_overrides,
    input_factory,
    invoke_import,
    import_contract,
    db_session,
):
    recipe = default_recipe(quantity_text="10g")
    if quantity_text == "一小撮":
        recipe["ingredients"]["测试食材"] = quantity_text
        recipe["ingredient_quantity_resolutions"] = {}
    paths = input_factory.create(
        recipes=[recipe],
        ingredients=[default_ingredient(**ingredient_overrides)],
    )

    with pytest.raises(Exception) as captured:
        invoke_import(paths, db_session)

    assert getattr(captured.value, "status_code", None) == 400
    assert row_count(db_session, import_contract.Recipe) == 0
    assert row_count(db_session, import_contract.RecipeNutrition) == 0


def test_不存在适用规则时禁止把毫升直接当克(
    input_factory,
    invoke_import,
    import_contract,
    db_session,
):
    paths = input_factory.create(
        recipes=[
            {
                **default_recipe(),
                "ingredients": {"测试食材": "10ml"},
                "ingredient_quantity_resolutions": {},
            }
        ],
    )

    with pytest.raises(Exception) as captured:
        invoke_import(paths, db_session)

    assert getattr(captured.value, "status_code", None) == 400
    assert row_count(db_session, import_contract.Recipe) == 0
