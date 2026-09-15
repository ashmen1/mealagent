from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Recipe


class RecipeRepositoryError(RuntimeError):
    """读取正式可推荐菜谱失败。"""


def load_recommendable_recipe_names(
    session: Session,
    recipe_names: list[str],
) -> set[str]:
    """返回指定名称中正式允许推荐的菜谱名。"""

    if not isinstance(session, Session):
        raise RecipeRepositoryError("数据库 Session 无效")
    if not recipe_names:
        return set()
    try:
        return set(
            session.scalars(
                select(Recipe.name).where(
                    Recipe.name.in_(recipe_names),
                    Recipe.is_recommendable.is_(True),
                )
            )
        )
    except Exception as exc:
        raise RecipeRepositoryError("查询正式可推荐菜谱失败") from exc


__all__ = ["RecipeRepositoryError", "load_recommendable_recipe_names"]
