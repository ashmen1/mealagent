from __future__ import annotations

import pytest
from sqlalchemy import func, select

from backend.infrastructure.database.models import Recipe

from .conftest import (
    InputPaths,
    REAL_DRI_PATH,
    REAL_INGREDIENT_PATH,
    REAL_PROFILE_PATH,
    REAL_RECIPE_PATH,
    default_ingredient,
    default_recipe,
)


def _build_recipe(
    *,
    minutes: int,
    step_count: int,
    ingredient_count: int,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    recipe = default_recipe()
    recipe["total_time_lower_bound_minutes"] = minutes
    recipe["atomic_steps"] = [
        {
            "atom_id": f"a{index}",
            "source_step_index": index,
            "text": f"执行步骤{index}",
            "duration_expression": None,
            "source_span": f"执行步骤{index}",
        }
        for index in range(step_count)
    ]
    names = [f"测试食材{index}" for index in range(ingredient_count)]
    recipe["ingredients"] = {name: "5g" for name in names}
    ingredients = [default_ingredient(name) for name in names]
    return recipe, ingredients


def test_Recipe难度列为非空三值枚举():
    column = Recipe.__table__.columns["difficulty"]
    check_sql = " ".join(
        str(constraint.sqltext)
        for constraint in Recipe.__table__.constraints
        if hasattr(constraint, "sqltext")
    )

    assert column.nullable is False
    assert "difficulty" in check_sql
    assert all(value in check_sql for value in ("简单", "中等", "复杂"))


@pytest.mark.parametrize(
    ("minutes", "steps", "ingredients", "expected"),
    [
        (20, 8, 8, "简单"),
        (21, 8, 9, "中等"),
        (20, 8, 9, "中等"),
        (20, 9, 9, "中等"),
        (60, 15, 18, "中等"),
        (61, 1, 1, "复杂"),
        (1, 16, 1, "复杂"),
        (1, 1, 19, "复杂"),
    ],
)
def test_导入时按时间步骤和食材种类派生难度(
    minutes,
    steps,
    ingredients,
    expected,
    input_factory,
    db_session,
    invoke_import,
):
    recipe, ingredient_rows = _build_recipe(
        minutes=minutes,
        step_count=steps,
        ingredient_count=ingredients,
    )
    paths = input_factory.create(
        recipes=[recipe],
        ingredients=ingredient_rows,
    )

    invoke_import(paths, db_session)

    assert db_session.scalar(
        select(Recipe.difficulty).where(Recipe.name == "测试菜品")
    ) == expected


def test_真实数据难度分布固定为323_1019_570(db_session, invoke_import):
    paths = InputPaths(
        REAL_RECIPE_PATH,
        REAL_INGREDIENT_PATH,
        REAL_PROFILE_PATH,
        REAL_DRI_PATH,
    )

    invoke_import(paths, db_session)

    distribution = dict(
        db_session.execute(
            select(Recipe.difficulty, func.count()).group_by(
                Recipe.difficulty
            )
        ).all()
    )
    assert distribution == {"简单": 323, "中等": 1019, "复杂": 570}
