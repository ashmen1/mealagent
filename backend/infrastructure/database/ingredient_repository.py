from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Ingredient


class IngredientRepositoryError(RuntimeError):
    """读取食材基础数据失败。"""


def load_ingredient_constraint_values(
    session: Session,
) -> tuple[set[str], set[str]]:
    """一次查询加载约束提取所需的标准食材名和非空类别。"""

    if not isinstance(session, Session):
        raise IngredientRepositoryError("数据库 Session 无效")

    try:
        rows = session.execute(
            select(Ingredient.name, Ingredient.category)
        ).all()
    except Exception as exc:
        raise IngredientRepositoryError("查询标准食材失败") from exc

    ingredient_names = {name for name, _ in rows}
    ingredient_categories = {
        category
        for _, category in rows
        if isinstance(category, str) and category.strip()
    }
    return ingredient_names, ingredient_categories


__all__ = [
    "IngredientRepositoryError",
    "load_ingredient_constraint_values",
]

