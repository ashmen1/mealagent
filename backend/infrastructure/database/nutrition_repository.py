from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ProfileDriTarget, Recipe, RecipeNutrition, UserProfile


class NutritionRepositoryError(RuntimeError):
    """读取菜谱营养或用户DRI失败。"""


@dataclass(frozen=True)
class RecipeNutritionRows:
    existing_names: set[str]
    nutrition_by_name: dict[str, RecipeNutrition]


@dataclass(frozen=True)
class ProfileTargetRows:
    profile: UserProfile | None
    targets: list[ProfileDriTarget]


def load_recipe_nutrition(
    session: Session,
    recipe_names: list[str],
) -> RecipeNutritionRows:
    if not isinstance(session, Session):
        raise NutritionRepositoryError("数据库 Session 无效")
    try:
        existing_names = set(
            session.scalars(
                select(Recipe.name).where(Recipe.name.in_(recipe_names))
            )
        )
        rows = session.execute(
            select(Recipe.name, RecipeNutrition)
            .join(RecipeNutrition, RecipeNutrition.recipe_id == Recipe.id)
            .where(Recipe.name.in_(recipe_names))
        )
        return RecipeNutritionRows(
            existing_names=existing_names,
            nutrition_by_name={name: nutrition for name, nutrition in rows},
        )
    except Exception as exc:
        raise NutritionRepositoryError("查询菜谱营养失败") from exc


def load_profile_targets(
    session: Session,
    profile_id: int,
    meal_period: str,
) -> ProfileTargetRows:
    if not isinstance(session, Session):
        raise NutritionRepositoryError("数据库 Session 无效")
    try:
        profile = session.get(UserProfile, profile_id)
        targets = list(
            session.scalars(
                select(ProfileDriTarget).where(
                    ProfileDriTarget.profile_id == profile_id,
                    ProfileDriTarget.meal_period == meal_period,
                )
            )
        )
        return ProfileTargetRows(profile=profile, targets=targets)
    except Exception as exc:
        raise NutritionRepositoryError("查询用户单餐营养目标失败") from exc


__all__ = [
    "NutritionRepositoryError",
    "ProfileTargetRows",
    "RecipeNutritionRows",
    "load_profile_targets",
    "load_recipe_nutrition",
]
