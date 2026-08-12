from .conftest import (
    REAL_DRI_PATH,
    REAL_INGREDIENT_PATH,
    REAL_PROFILE_PATH,
    REAL_RECIPE_PATH,
    InputPaths,
)
from sqlalchemy import select

from backend.infrastructure.database.models import Recipe


def test_全量真实数据成功导入并生成派生数据(db_session, invoke_import):
    paths = InputPaths(
        REAL_RECIPE_PATH,
        REAL_INGREDIENT_PATH,
        REAL_PROFILE_PATH,
        REAL_DRI_PATH,
    )

    result = invoke_import(paths, db_session)

    assert result["counts"] == {
        "recipes": 1912,
        "ingredients": 1245,
        "recipe_ingredients": 16262,
        "user_profiles": 50,
        "recipe_nutrition": 1912,
        "profile_dri_targets": 1350,
    }
    assert db_session.scalar(
        select(Recipe.id).where(Recipe.name == "果蔬清洗")
    ) is None
