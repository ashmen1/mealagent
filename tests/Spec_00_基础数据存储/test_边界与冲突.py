from __future__ import annotations

import copy
import json
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .conftest import default_ingredient, default_profile, default_recipe, table_count


def test_一道菜的多种食材分别写入关联表(input_factory, db_session, invoke_import):
    recipe = default_recipe()
    recipe["ingredients"] = {"食材甲": "5g", "食材乙": "1个", "食材丙": "2片"}
    ingredients = [default_ingredient(name) for name in recipe["ingredients"]]
    paths = input_factory.create(recipes=[recipe], ingredients=ingredients)

    result = invoke_import(paths, db_session)

    assert result == {
        "counts": {
            "recipes": 1,
            "ingredients": 3,
            "recipe_ingredients": 3,
            "user_profiles": 1,
            "recipe_nutrition": 1,
            "profile_dri_targets": 27,
        }
    }
    assert table_count(db_session, "recipe_ingredients") == 3


def test_食材表为营养表与菜品食材并集且缺失营养保存null(
    input_factory, db_session, invoke_import
):
    recipe = default_recipe()
    recipe["ingredients"] = {"菜品专用食材": "8g"}
    used_ingredient = default_ingredient("菜品专用食材")
    nutrition_only = default_ingredient("仅营养表食材")
    for field in (
        "energy_kcal",
        "protein_g",
        "fat_g",
        "carbohydrate_g",
        "fiber_g",
        "sodium_mg",
        "calcium_mg",
        "iron_mg",
        "cholesterol_mg",
    ):
        nutrition_only[field] = ""
    paths = input_factory.create(
        recipes=[recipe], ingredients=[used_ingredient, nutrition_only]
    )

    result = invoke_import(paths, db_session)

    assert result["counts"]["ingredients"] == 2
    rows = db_session.execute(
        text(
            "SELECT name, energy_kcal, protein_g, fat_g, carbohydrate_g, fiber_g, "
            "sodium_mg, calcium_mg, iron_mg, cholesterol_mg FROM ingredients"
        )
    ).mappings().all()
    by_name = {row["name"]: row for row in rows}
    assert set(by_name) == {"菜品专用食材", "仅营养表食材"}
    assert all(
        value is None
        for key, value in by_name["仅营养表食材"].items()
        if key != "name"
    )
    assert by_name["菜品专用食材"]["energy_kcal"] == Decimal("100.5")


def test_只换算能够明确确定的质量值(input_factory, db_session, invoke_import):
    quantities = {
        "精确克数食材": "5g",
        "中文克数食材": "1.25克",
        "按个食材": "1个",
        "按勺食材": "1勺",
        "按片食材": "2片",
        "液体食材": "100毫升",
        "范围食材": "0.5-1g",
    }
    recipe = default_recipe()
    recipe["ingredients"] = quantities
    ingredients = [default_ingredient(name) for name in quantities]
    paths = input_factory.create(recipes=[recipe], ingredients=ingredients)

    invoke_import(paths, db_session)

    rows = db_session.execute(
        text(
            "SELECT i.name, ri.quantity_text, ri.quantity_g "
            "FROM recipe_ingredients ri "
            "JOIN ingredients i ON i.id = ri.ingredient_id"
        )
    ).mappings().all()
    by_name = {row["name"]: row for row in rows}
    assert by_name["精确克数食材"]["quantity_g"] == Decimal("5")
    assert by_name["中文克数食材"]["quantity_g"] == Decimal("1.25")
    for name in ["按个食材", "按勺食材", "按片食材", "液体食材", "范围食材"]:
        assert by_name[name]["quantity_g"] is None
        assert by_name[name]["quantity_text"] == quantities[name]


def _insert_recipe_and_ingredient(session):
    recipe_id = session.execute(
        text(
            "INSERT INTO recipes "
            "(name, total_time_lower_bound_minutes, dish_type, atomic_steps, "
            "labels, difficulty) "
            "VALUES ('外键测试菜', 0, '菜', CAST('[]' AS JSON), "
            "CAST('[]' AS JSON), '简单') "
            "RETURNING id"
        )
    ).scalar_one()
    ingredient_id = session.execute(
        text(
            "INSERT INTO ingredients (name, aliases) "
            "VALUES ('外键测试食材', CAST('[]' AS JSON)) RETURNING id"
        )
    ).scalar_one()
    session.flush()
    return recipe_id, ingredient_id


@pytest.mark.parametrize("missing_side", ["recipe", "ingredient"])
def test_关联表引用不存在的主记录时数据库拒绝(missing_side, db_session):
    recipe_id, ingredient_id = _insert_recipe_and_ingredient(db_session)
    if missing_side == "recipe":
        recipe_id += 999999
    else:
        ingredient_id += 999999

    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO recipe_ingredients "
                "(recipe_id, ingredient_id, quantity_text, quantity_g, "
                "resolved_quantity_g, is_quantity_estimated) "
                "VALUES (:recipe_id, :ingredient_id, '1个', NULL, 10, true)"
            ),
            {"recipe_id": recipe_id, "ingredient_id": ingredient_id},
        )
        db_session.flush()


@pytest.mark.parametrize("duplicate_kind", ["recipe", "ingredient", "profile"])
def test_输入内重复唯一值时返回409且不静默覆盖(
    duplicate_kind, input_factory, db_session, assert_import_error
):
    recipes = [default_recipe()]
    ingredients = [default_ingredient()]
    profiles = [default_profile()]
    if duplicate_kind == "recipe":
        recipes.append(copy.deepcopy(recipes[0]))
    elif duplicate_kind == "ingredient":
        ingredients.append(copy.deepcopy(ingredients[0]))
    else:
        profiles.append(copy.deepcopy(profiles[0]))
    paths = input_factory.create(
        recipes=recipes, ingredients=ingredients, profiles=profiles
    )

    assert_import_error(paths, db_session, 409)
    assert all(
        table_count(db_session, table_name) == 0
        for table_name in [
            "recipes",
            "ingredients",
            "recipe_ingredients",
            "user_profiles",
        ]
    )


def test_关联表联合主键重复时数据库拒绝(db_session):
    recipe_id, ingredient_id = _insert_recipe_and_ingredient(db_session)
    statement = text(
        "INSERT INTO recipe_ingredients "
        "(recipe_id, ingredient_id, quantity_text, quantity_g, "
        "resolved_quantity_g, is_quantity_estimated) "
        "VALUES (:recipe_id, :ingredient_id, '5g', 5, 5, false)"
    )
    params = {"recipe_id": recipe_id, "ingredient_id": ingredient_id}
    db_session.execute(statement, params)
    db_session.flush()

    with pytest.raises(IntegrityError):
        db_session.execute(statement, params)
        db_session.flush()


def test_同一批数据再次导入返回409且原数据不变(
    input_factory, db_session, invoke_import, assert_import_error
):
    paths = input_factory.create()
    first_result = invoke_import(paths, db_session)
    before = {
        table_name: table_count(db_session, table_name)
        for table_name in first_result["counts"]
    }

    assert_import_error(paths, db_session, 409)

    after = {
        table_name: table_count(db_session, table_name)
        for table_name in first_result["counts"]
    }
    assert after == before


def test_所有空数组与空对象按非null值保存(input_factory, db_session, invoke_import):
    recipe = default_recipe()
    recipe["atomic_steps"] = []
    recipe["labels"] = []
    ingredient = default_ingredient()
    ingredient["别名"] = ""
    profile = default_profile()
    profile["特殊人群"] = []
    profile["过敏食材"] = []
    profile["健康需求"] = []
    profile["体检指标"] = {}
    paths = input_factory.create(
        recipes=[recipe], ingredients=[ingredient], profiles=[profile]
    )

    invoke_import(paths, db_session)

    saved_recipe = db_session.execute(
        text("SELECT atomic_steps, labels FROM recipes WHERE name = '测试菜品'")
    ).mappings().one()
    saved_aliases = db_session.execute(
        text("SELECT aliases FROM ingredients WHERE name = '测试食材'")
    ).scalar_one()
    saved_profile = db_session.execute(
        text(
            "SELECT special_populations, allergens, health_goals, medical_metrics "
            "FROM user_profiles WHERE id = 9001"
        )
    ).mappings().one()
    assert saved_recipe == {"atomic_steps": [], "labels": []}
    assert saved_aliases == []
    assert saved_profile == {
        "special_populations": [],
        "allergens": [],
        "health_goals": [],
        "medical_metrics": {},
    }


def test_归一化值原样保存且不再次处理(input_factory, db_session, invoke_import):
    recipe = default_recipe()
    recipe["name"] = "原样菜名-A"
    recipe["ingredients"] = {"青红椒/测试": "3g"}
    recipe["labels"] = ["低GI-原值"]
    ingredient = default_ingredient("青红椒/测试")
    profile = default_profile()
    profile["口味偏好"] = "酸、甜-原值"
    paths = input_factory.create(
        recipes=[recipe], ingredients=[ingredient], profiles=[profile]
    )

    invoke_import(paths, db_session)

    saved_recipe = db_session.execute(
        text("SELECT name, labels FROM recipes")
    ).mappings().one()
    saved_ingredient = db_session.execute(
        text("SELECT name FROM ingredients")
    ).scalar_one()
    saved_taste = db_session.execute(
        text("SELECT taste_preference FROM user_profiles")
    ).scalar_one()
    assert saved_recipe == {"name": "原样菜名-A", "labels": ["低GI-原值"]}
    assert saved_ingredient == "青红椒/测试"
    assert saved_taste == "酸、甜-原值"


def test_模型定义基础数据与营养派生表(production_contract):
    assert {
        "recipes",
        "ingredients",
        "recipe_ingredients",
        "user_profiles",
        "recipe_nutrition",
        "profile_dri_targets",
    } <= set(production_contract.Base.metadata.tables)
