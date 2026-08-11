from __future__ import annotations

import pytest

from spec05_support import InputPaths, REPO_ROOT, row_count


@pytest.mark.integration
def test_真实静态数据完整导入并生成全部派生数据(
    db_session,
    import_contract,
    invoke_import,
):
    paths = InputPaths(
        recipes=REPO_ROOT / "datas" / "processed" / "Recipes" / "RecipeComplete.json",
        ingredients=REPO_ROOT / "datas" / "processed" / "Ingredients" / "Ingredients2Nutrition.csv",
        profiles=REPO_ROOT / "datas" / "processed" / "users" / "50个用户健康档案_归一化.json",
        dri=REPO_ROOT / "datas" / "processed" / "Nutrition" / "DRI2023.csv",
    )

    result = invoke_import(paths, db_session)

    assert result["counts"] == {
        "recipes": 1913,
        "ingredients": 1245,
        "recipe_ingredients": 16263,
        "user_profiles": 50,
        "recipe_nutrition": 1913,
        "profile_dri_targets": 1350,
    }
    assert row_count(db_session, import_contract.RecipeIngredient) == 16263
    assert row_count(db_session, import_contract.RecipeNutrition) == 1913
    assert row_count(db_session, import_contract.ProfileDriTarget) == 1350
