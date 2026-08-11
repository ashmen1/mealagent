from __future__ import annotations

from decimal import Decimal

from spec05_support import (
    NUTRIENT_FIELDS,
    lunch_targets,
    row_count,
)


def test_基础数据导入同时生成菜谱营养和三餐DRI(
    input_factory,
    invoke_import,
    import_contract,
    db_session,
):
    paths = input_factory.create()

    result = invoke_import(paths, db_session)

    assert result["counts"] == {
        "recipes": 1,
        "ingredients": 1,
        "recipe_ingredients": 1,
        "user_profiles": 1,
        "recipe_nutrition": 1,
        "profile_dri_targets": 27,
    }
    assert row_count(db_session, import_contract.RecipeNutrition) == 1
    assert row_count(db_session, import_contract.ProfileDriTarget) == 27


def test_按输入顺序返回多条菜谱整份营养(service_context):
    result = service_context.service.get_recipe_nutrition(["菜谱乙", "菜谱甲"])

    assert [item["recipe_name"] for item in result] == ["菜谱乙", "菜谱甲"]
    assert result[0] == {
        "recipe_name": "菜谱乙",
        "energy_kcal": Decimal("20.02"),
        "protein_g": Decimal("1.02"),
        "fat_g": Decimal("2.02"),
        "carbohydrate_g": Decimal("3.02"),
        "fiber_g": Decimal("4.02"),
        "sodium_mg": Decimal("5.02"),
        "calcium_mg": Decimal("6.02"),
        "iron_mg": Decimal("7.02"),
        "cholesterol_mg": Decimal("8.02"),
    }


def test_返回指定用户午餐的九项营养目标(service_context):
    result = service_context.service.get_meal_nutrition_targets(25, "午餐")

    assert result == {
        "profile_id": 25,
        "meal_period": "午餐",
        "nutrients": lunch_targets(),
    }
    assert tuple(result["nutrients"]) == NUTRIENT_FIELDS
