from __future__ import annotations

import copy
from decimal import Decimal

import pytest
from sqlalchemy import text

from conftest import CSV_FIELDS, default_profile, default_recipe


@pytest.mark.parametrize("bad_content", ["", "[{", "not-json"])
def test_菜品文件为空或不是合法json时返回400(
    bad_content, input_factory, db_session, assert_import_error
):
    paths = input_factory.create()
    paths.recipes.write_text(bad_content, encoding="utf-8")

    assert_import_error(paths, db_session, 400)


def test_食材csv缺少必需表头时返回400(
    input_factory, db_session, assert_import_error
):
    paths = input_factory.create()
    fields = [field for field in CSV_FIELDS if field != "标准食材名"]
    input_factory.write_csv(paths.ingredients, [{"英文名": "Missing name"}], fields)

    assert_import_error(paths, db_session, 400)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("name", None),
        ("name", ""),
        ("total_time_lower_bound_minutes", None),
        ("total_time_lower_bound_minutes", "10"),
        ("atomic_steps", None),
        ("atomic_steps", {}),
        ("labels", None),
        ("labels", {}),
        ("ingredients", None),
        ("ingredients", []),
        ("dish_type", "饮品"),
        ("dish_type", ""),
    ],
)
def test_菜品必填字段为空或类型错误时返回400(
    field, bad_value, input_factory, db_session, assert_import_error
):
    recipe = default_recipe()
    recipe[field] = bad_value
    paths = input_factory.create(recipes=[recipe])

    assert_import_error(paths, db_session, 400)


@pytest.mark.parametrize(
    "field",
    [
        "name",
        "ingredients",
        "total_time_lower_bound_minutes",
        "atomic_steps",
        "labels",
    ],
)
def test_菜品缺少任一必填字段时返回400(
    field, input_factory, db_session, assert_import_error
):
    recipe = default_recipe()
    recipe.pop(field)
    paths = input_factory.create(recipes=[recipe])

    assert_import_error(paths, db_session, 400)


@pytest.mark.parametrize("quantity", [None, ""])
def test_食材最终数量文本为空时返回400(
    quantity, input_factory, db_session, assert_import_error
):
    recipe = default_recipe()
    recipe["ingredients"]["测试食材"] = quantity
    paths = input_factory.create(recipes=[recipe])

    assert_import_error(paths, db_session, 400)


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "性别",
        "年龄",
        "劳动强度",
        "特殊人群",
        "口味偏好",
        "过敏食材",
        "健康需求",
        "身高_cm",
        "体重_kg",
        "BMI",
        "体检指标",
    ],
)
def test_用户档案缺少任一必填字段时返回400(
    field, input_factory, db_session, assert_import_error
):
    profile = default_profile()
    profile.pop(field)
    paths = input_factory.create(profiles=[profile])

    assert_import_error(paths, db_session, 400)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("id", None),
        ("性别", ""),
        ("年龄", "30"),
        ("劳动强度", ""),
        ("特殊人群", None),
        ("特殊人群", {}),
        ("口味偏好", ""),
        ("过敏食材", None),
        ("健康需求", None),
        ("身高_cm", "165"),
        ("体重_kg", "55"),
        ("BMI", "20.2"),
        ("体检指标", None),
        ("体检指标", []),
    ],
)
def test_用户档案必填字段为空或类型错误时返回400(
    field, bad_value, input_factory, db_session, assert_import_error
):
    profile = default_profile()
    profile[field] = bad_value
    paths = input_factory.create(profiles=[profile])

    assert_import_error(paths, db_session, 400)


@pytest.mark.parametrize(
    ("minutes", "is_valid"),
    [(0, True), (-1, False)],
)
def test_烹饪时间下界(minutes, is_valid, input_factory, db_session, invoke_import, assert_import_error):
    recipe = default_recipe()
    recipe["total_time_lower_bound_minutes"] = minutes
    paths = input_factory.create(recipes=[recipe])

    if is_valid:
        result = invoke_import(paths, db_session)
        assert result["counts"]["recipes"] == 1
    else:
        assert_import_error(paths, db_session, 400)


@pytest.mark.parametrize(
    ("field", "value", "is_valid"),
    [
        ("年龄", 1, True),
        ("年龄", 0, False),
        ("身高_cm", Decimal("0.01"), True),
        ("身高_cm", 0, False),
        ("身高_cm", -1, False),
        ("体重_kg", Decimal("0.01"), True),
        ("体重_kg", 0, False),
        ("体重_kg", -1, False),
        ("BMI", Decimal("0.01"), True),
        ("BMI", 0, False),
        ("BMI", -1, False),
    ],
)
def test_用户数值字段下界(
    field, value, is_valid, input_factory, db_session, invoke_import, assert_import_error
):
    profile = default_profile()
    profile[field] = float(value) if isinstance(value, Decimal) else value
    paths = input_factory.create(profiles=[profile])

    if is_valid:
        result = invoke_import(paths, db_session)
        assert result["counts"]["user_profiles"] == 1
    else:
        assert_import_error(paths, db_session, 400)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [("性别", "未知"), ("劳动强度", "极高")],
)
def test_用户枚举值越界时返回400(
    field, bad_value, input_factory, db_session, assert_import_error
):
    profile = default_profile()
    profile[field] = bad_value
    paths = input_factory.create(profiles=[profile])

    assert_import_error(paths, db_session, 400)


@pytest.mark.parametrize(
    "profile",
    [
        {**default_profile(), "特殊人群": ["孕妇"], "孕周期": None},
        {**default_profile(), "特殊人群": [], "孕周期": "12周"},
    ],
)
def test_孕妇身份与孕周冲突时返回400(
    profile, input_factory, db_session, assert_import_error
):
    paths = input_factory.create(profiles=[copy.deepcopy(profile)])

    assert_import_error(paths, db_session, 400)


def test_规格允许为空的字段保存为null(input_factory, db_session, invoke_import):
    ingredient = {
        "标准食材名": "无营养测试食材",
        "英文名": "",
        "分类": "",
        "USDA描述": "",
        "USDA_FDC_ID": "",
        "energy_kcal": "",
        "protein_g": "",
        "fat_g": "",
        "carbohydrate_g": "",
        "fiber_g": "",
        "sodium_mg": "",
        "calcium_mg": "",
        "iron_mg": "",
        "cholesterol_mg": "",
        "别名": "",
    }
    recipe = default_recipe()
    recipe["ingredients"] = {"无营养测试食材": "1个"}
    paths = input_factory.create(recipes=[recipe], ingredients=[ingredient])

    invoke_import(paths, db_session)

    row = db_session.execute(
        text(
            "SELECT english_name, category, energy_kcal, protein_g, fat_g, "
            "carbohydrate_g, fiber_g, sodium_mg, calcium_mg, iron_mg, cholesterol_mg "
            "FROM ingredients WHERE name = '无营养测试食材'"
        )
    ).mappings().one()
    assert all(value is None for value in row.values())
    profile_week = db_session.execute(
        text("SELECT gestational_week FROM user_profiles WHERE id = 9001")
    ).scalar_one()
    assert profile_week is None


def test_菜品dish_type缺失时保存为null(input_factory, db_session, invoke_import):
    recipe = default_recipe()
    recipe.pop("dish_type")
    paths = input_factory.create(recipes=[recipe])

    invoke_import(paths, db_session)

    stored = db_session.execute(
        text(
            "SELECT dish_type FROM recipes WHERE name = '测试菜品'"
        )
    ).scalar_one()
    assert stored is None

