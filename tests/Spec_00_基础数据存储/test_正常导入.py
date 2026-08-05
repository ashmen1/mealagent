from decimal import Decimal

from sqlalchemy import text

from conftest import (
    REAL_INGREDIENT_PATH,
    REAL_PROFILE_PATH,
    REAL_RECIPE_PATH,
    InputPaths,
    table_count,
)


def test_全量真实数据正常导入并返回四表精确计数(db_session, invoke_import):
    paths = InputPaths(REAL_RECIPE_PATH, REAL_INGREDIENT_PATH, REAL_PROFILE_PATH)

    result = invoke_import(paths, db_session)

    expected_counts = {
        "recipes": 1914,
        "ingredients": 1245,
        "recipe_ingredients": 16269,
        "user_profiles": 50,
    }
    assert result == {"counts": expected_counts}
    assert {
        table_name: table_count(db_session, table_name)
        for table_name in expected_counts
    } == expected_counts

    recipe = db_session.execute(
        text(
            "SELECT id, atomic_steps, labels "
            "FROM recipes WHERE name = :name"
        ),
        {"name": "秋梨膏"},
    ).mappings().one()
    assert recipe["id"] > 0
    assert isinstance(recipe["atomic_steps"], list)
    assert recipe["labels"] == ["下午茶", "晚餐", "地方风味", "甜", "儿童", "老人", "助眠"]

    ingredient = db_session.execute(
        text(
            "SELECT aliases FROM ingredients WHERE name = :name"
        ),
        {"name": "五花肉"},
    ).mappings().one()
    assert "去皮猪五花肉" in ingredient["aliases"]

    no_nutrition = db_session.execute(
        text(
            "SELECT english_name, category, energy_kcal, protein_g, fat_g, "
            "carbohydrate_g, fiber_g, sodium_mg, calcium_mg, iron_mg, cholesterol_mg "
            "FROM ingredients WHERE name = :name"
        ),
        {"name": "葡萄糖酸内酯"},
    ).mappings().one()
    assert no_nutrition["english_name"] is None
    assert no_nutrition["category"] == "调料"
    assert all(
        no_nutrition[field_name] is None
        for field_name in (
            "energy_kcal",
            "protein_g",
            "fat_g",
            "carbohydrate_g",
            "fiber_g",
            "sodium_mg",
            "calcium_mg",
            "iron_mg",
            "cholesterol_mg",
        )
    )

    quantities = db_session.execute(
        text(
            "SELECT i.name, ri.quantity_text, ri.quantity_g "
            "FROM recipe_ingredients ri "
            "JOIN recipes r ON r.id = ri.recipe_id "
            "JOIN ingredients i ON i.id = ri.ingredient_id "
            "WHERE r.name = :recipe_name AND i.name IN (:mass_name, :unit_name)"
        ),
        {
            "recipe_name": "秋梨膏",
            "mass_name": "梨肉",
            "unit_name": "罗汉果",
        },
    ).mappings().all()
    quantities_by_name = {row["name"]: row for row in quantities}
    assert quantities_by_name["梨肉"]["quantity_text"] == "1000g"
    assert quantities_by_name["梨肉"]["quantity_g"] == Decimal("1000")
    assert quantities_by_name["罗汉果"]["quantity_text"] == "1个"
    assert quantities_by_name["罗汉果"]["quantity_g"] is None

    # print("\n")
    # print("test")

    profile = db_session.execute(
        text(
            "SELECT special_populations, gestational_week, allergens, "
            "health_goals, medical_metrics FROM user_profiles WHERE id = 2"
        )
    ).mappings().one()
    assert profile["special_populations"] == ["孕妇"]
    assert profile["gestational_week"] == 22
    assert profile["allergens"] == []
    assert profile["health_goals"] == ["补钙", "补铁", "均衡营养"]
    assert isinstance(profile["medical_metrics"], dict)
