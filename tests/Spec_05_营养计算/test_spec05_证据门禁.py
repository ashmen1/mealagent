from __future__ import annotations

import copy

import pytest

from spec05_support import (
    default_recipe,
    quantity_resolution,
    row_count,
)


@pytest.mark.parametrize(
    "missing_field",
    [
        "resolved_quantity_g",
        "calculation_path",
        "reference_source",
        "ingredient_weight_distribution",
    ],
)
def test_最终克重缺少必需字段时整批拒绝(
    missing_field,
    input_factory,
    invoke_import,
    import_contract,
    db_session,
):
    recipe = default_recipe()
    recipe["ingredient_quantity_resolutions"]["测试食材"].pop(missing_field)
    paths = input_factory.create(recipes=[recipe])

    with pytest.raises(Exception) as captured:
        invoke_import(paths, db_session)

    assert getattr(captured.value, "status_code", None) == 400
    assert row_count(db_session, import_contract.Recipe) == 0


def test_最终克重必须与菜谱食材一一对应(
    input_factory,
    invoke_import,
    import_contract,
    db_session,
):
    recipe = default_recipe()
    recipe["ingredient_quantity_resolutions"]["额外食材"] = quantity_resolution("1g", 1)
    paths = input_factory.create(recipes=[recipe])

    with pytest.raises(Exception) as captured:
        invoke_import(paths, db_session)

    assert getattr(captured.value, "status_code", None) == 400
    assert "一一对应" in str(captured.value)
    assert row_count(db_session, import_contract.Recipe) == 0


def test_最终克重原始用量必须与菜谱一致(
    input_factory,
    invoke_import,
    import_contract,
    db_session,
):
    recipe = default_recipe(quantity_text="10g")
    recipe["ingredient_quantity_resolutions"]["测试食材"]["original_quantity"] = "20g"
    paths = input_factory.create(recipes=[recipe])

    with pytest.raises(Exception) as captured:
        invoke_import(paths, db_session)

    assert getattr(captured.value, "status_code", None) == 400
    assert "原始数量" in str(captured.value)
    assert row_count(db_session, import_contract.Recipe) == 0


@pytest.mark.parametrize(
    "mutation",
    [
        {"resolved_quantity_g": 0},
        {"is_quantity_estimated": "true"},
        {"is_nutrition_excluded": "false"},
    ],
)
def test_非排除克重必须为正数且标记必须为布尔值(
    mutation,
    input_factory,
    invoke_import,
    import_contract,
    db_session,
):
    recipe = default_recipe()
    recipe["ingredient_quantity_resolutions"]["测试食材"].update(mutation)
    paths = input_factory.create(recipes=[recipe])

    with pytest.raises(Exception) as captured:
        invoke_import(paths, db_session)

    assert getattr(captured.value, "status_code", None) == 400
    assert row_count(db_session, import_contract.Recipe) == 0


def test_克重分布分位值倒置时整批拒绝(
    input_factory,
    invoke_import,
    import_contract,
    db_session,
):
    recipe = default_recipe()
    distribution = recipe["ingredient_quantity_resolutions"]["测试食材"][
        "ingredient_weight_distribution"
    ]
    distribution["p25_g"] = 20
    distribution["median_g"] = 10
    paths = input_factory.create(recipes=[recipe])

    with pytest.raises(Exception) as captured:
        invoke_import(paths, db_session)

    assert getattr(captured.value, "status_code", None) == 400
    assert "分位值顺序" in str(captured.value)
    assert row_count(db_session, import_contract.Recipe) == 0


def test_当前菜谱明确质量允许标记为精确(
    input_factory,
    invoke_import,
    import_contract,
    db_session,
):
    recipe = default_recipe(quantity_text="30g", resolved_grams=30)
    paths = input_factory.create(recipes=[recipe])

    invoke_import(paths, db_session)

    association = db_session.query(import_contract.RecipeIngredient).one()
    assert str(association.resolved_quantity_g) == "30.00"
    assert association.is_quantity_estimated is False


def test_无权威来源估算保留取值路径且可参与计算(
    input_factory,
    invoke_import,
    import_contract,
    db_session,
):
    recipe = default_recipe(
        quantity_text="适量",
        resolved_grams=5,
        is_estimated=True,
    )
    resolution = recipe["ingredient_quantity_resolutions"]["测试食材"]
    resolution["calculation_path"] = (
        "原始适量 → 同食材严格质量样本3条 → strict_mass_mode → 5.00g"
    )
    resolution["reference_source"] = "项目内部统计；trace#测试食材"
    paths = input_factory.create(recipes=[recipe])

    invoke_import(paths, db_session)

    association = db_session.query(import_contract.RecipeIngredient).one()
    assert str(association.resolved_quantity_g) == "5.00"
    assert association.is_quantity_estimated is True


def test_输入工厂不篡改显式提供的最终克重记录(input_factory):
    recipe = default_recipe()
    expected = copy.deepcopy(recipe["ingredient_quantity_resolutions"])

    paths = input_factory.create(recipes=[recipe])

    assert paths.recipes.exists()
    assert recipe["ingredient_quantity_resolutions"] == expected
