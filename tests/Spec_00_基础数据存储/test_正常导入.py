from .conftest import (
    REAL_DRI_PATH,
    REAL_INGREDIENT_PATH,
    REAL_PROFILE_PATH,
    REAL_RECIPE_PATH,
    InputPaths,
)


def test_全量真实数据成功导入并生成派生数据(db_session, invoke_import):
    paths = InputPaths(
        REAL_RECIPE_PATH,
        REAL_INGREDIENT_PATH,
        REAL_PROFILE_PATH,
        REAL_DRI_PATH,
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
