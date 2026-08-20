from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from spec05_support import (
    assert_status_code,
    default_dri_rule,
    default_profile,
    default_recipe,
    row_count,
)


@pytest.mark.parametrize(
    "recipe_names",
    [[], [""], ["菜谱甲", "菜谱甲"]],
)
def test_菜谱名列表为空_含空值或重复时返回400(
    recipe_names,
    service_context,
):
    assert_status_code(
        lambda: service_context.service.get_recipe_nutrition(recipe_names),
        400,
    )


def test_任一菜谱不存在时整体返回404不返回部分结果(service_context):
    assert_status_code(
        lambda: service_context.service.get_recipe_nutrition(
            ["菜谱甲", "不存在菜谱"]
        ),
        404,
    )


@pytest.mark.parametrize(
    ("profile_id", "meal_period"),
    [(0, "早餐"), (25, "下午茶"), (25, "夜宵")],
)
def test_非法用户ID或不支持餐次返回400(
    profile_id,
    meal_period,
    service_context,
):
    assert_status_code(
        lambda: service_context.service.get_meal_nutrition_targets(
            profile_id, meal_period
        ),
        400,
    )


def test_用户档案不存在时返回404(service_context):
    assert_status_code(
        lambda: service_context.service.get_meal_nutrition_targets(999, "午餐"),
        404,
    )


@pytest.mark.parametrize(
    "profile",
    [
        default_profile(性别="女", 年龄=50, 是否有月经=None),
        default_profile(性别="男", 特殊人群=["孕妇"], 孕周期="12周"),
        default_profile(性别="女", 特殊人群=["孕妇"], 孕周期="0周"),
        default_profile(性别="女", 特殊人群=["孕妇"], 孕周期="43周"),
        default_profile(
            性别="女",
            特殊人群=["孕妇", "哺乳期"],
            孕周期="20周",
        ),
        default_profile(年龄=65, 劳动强度="高"),
    ],
)
def test_不支持的档案组合导致整批导入返回400并回滚(
    profile,
    input_factory,
    invoke_import,
    import_contract,
    db_session,
):
    paths = input_factory.create(
        profiles=[profile],
        dri_rules=[
            default_dri_rule(
                性别=profile["性别"],
                年龄下限=18,
                年龄上限=100,
                劳动强度=profile["劳动强度"],
            )
        ],
    )

    with pytest.raises(Exception) as captured:
        invoke_import(paths, db_session)

    assert getattr(captured.value, "status_code", None) == 400
    assert row_count(db_session, import_contract.UserProfile) == 0
    assert row_count(db_session, import_contract.ProfileDriTarget) == 0


def test_重复主键导致409且不写入部分派生数据(
    input_factory,
    invoke_import,
    import_contract,
    db_session,
):
    first_paths = input_factory.create()
    second_paths = input_factory.create(
        recipes=[default_recipe(name="另一菜谱")],
        profiles=[default_profile(id=25)],
    )
    invoke_import(first_paths, db_session)

    with pytest.raises(Exception) as captured:
        invoke_import(second_paths, db_session)

    assert getattr(captured.value, "status_code", None) == 409
    assert row_count(db_session, import_contract.RecipeNutrition) == 1
    assert row_count(db_session, import_contract.ProfileDriTarget) == 27


def test_数据库拒绝非正数解析克重(import_contract, db_session):
    recipe = import_contract.Recipe(
        name="数据库约束测试菜",
        is_recommendable=True,
        total_time_lower_bound_minutes=0,
        dish_type="菜",
        atomic_steps=[],
        labels=[],
        difficulty="简单",
    )
    ingredient = import_contract.Ingredient(
        name="数据库约束测试食材",
        aliases=[],
    )
    db_session.add_all([recipe, ingredient])
    db_session.flush()
    db_session.add(
        import_contract.RecipeIngredient(
            recipe_id=recipe.id,
            ingredient_id=ingredient.id,
            quantity_text="0g",
            quantity_g=Decimal("0"),
            resolved_quantity_g=Decimal("0"),
            is_quantity_estimated=False,
            is_nutrition_excluded=False,
        )
    )

    with pytest.raises(IntegrityError):
        db_session.flush()


class BrokenSessionFactory:
    def __call__(self):
        raise SQLAlchemyError("数据库不可达")


def test_菜谱营养数据库查询失败返回500(service_contract):
    service = service_contract.NutritionService(BrokenSessionFactory())
    assert_status_code(
        lambda: service.get_recipe_nutrition(["菜谱甲"]),
        500,
    )


def test_单餐目标数据库查询失败返回500(service_contract):
    service = service_contract.NutritionService(BrokenSessionFactory())
    assert_status_code(
        lambda: service.get_meal_nutrition_targets(25, "午餐"),
        500,
    )
