import pytest

from .conftest import (
    REAL_DRI_PATH,
    REAL_INGREDIENT_PATH,
    REAL_PROFILE_PATH,
    REAL_RECIPE_PATH,
    InputPaths,
    default_recipe,
)
from sqlalchemy import select

from backend.infrastructure.database.models import Recipe


@pytest.mark.parametrize("is_recommendable", [True, False])
def test_推荐资格按布尔原值写入非空数据库列(
    is_recommendable,
    input_factory,
    db_session,
    invoke_import,
):
    recipe = default_recipe()
    recipe["is_recommendable"] = is_recommendable
    paths = input_factory.create(recipes=[recipe])

    invoke_import(paths, db_session)

    stored = db_session.scalar(select(Recipe).where(Recipe.name == "测试菜品"))
    assert stored is not None
    assert stored.is_recommendable is is_recommendable
    assert Recipe.__table__.c.is_recommendable.nullable is False


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
