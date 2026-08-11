from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from backend.core.nutrition_contract import (
    NUTRIENT_FIELDS,
    MealNutritionTargetsResult,
    NutrientTargetResult,
    NutritionCalculationError,
    RecipeNutritionResult,
    validate_profile_and_meal,
    validate_recipe_names,
)
from backend.infrastructure.database.nutrition_repository import (
    ProfileTargetRows,
    RecipeNutritionRows,
    load_profile_targets,
    load_recipe_nutrition,
)

if TYPE_CHECKING:
    from backend.infrastructure.database.models import (
        ProfileDriTarget,
        RecipeNutrition,
    )


SessionFactory = Callable[[], Session]
RecipeLoader = Callable[[Session, list[str]], RecipeNutritionRows]
TargetLoader = Callable[[Session, int, str], ProfileTargetRows]


class NutritionService:
    """查询预计算的菜谱营养和用户单餐营养目标。"""

    def __init__(
        self,
        session_factory: SessionFactory,
        recipe_loader: RecipeLoader = load_recipe_nutrition,
        target_loader: TargetLoader = load_profile_targets,
    ) -> None:
        if not callable(session_factory):
            raise NutritionCalculationError(500, "Session工厂无效")
        if not callable(recipe_loader) or not callable(target_loader):
            raise NutritionCalculationError(500, "营养Repository无效")
        self._session_factory = session_factory
        self._recipe_loader = recipe_loader
        self._target_loader = target_loader

    def get_recipe_nutrition(
        self,
        recipe_names: list[str],
    ) -> list[RecipeNutritionResult]:
        validated_names = validate_recipe_names(recipe_names)
        try:
            with self._session_factory() as session:
                loaded = self._recipe_loader(session, validated_names)
        except NutritionCalculationError:
            raise
        except Exception as exc:
            raise NutritionCalculationError(500, "查询菜谱营养失败") from exc

        missing = [name for name in validated_names if name not in loaded.existing_names]
        if missing:
            raise NutritionCalculationError(404, f"菜谱不存在：{', '.join(missing)}")
        incomplete = [
            name for name in validated_names if name not in loaded.nutrition_by_name
        ]
        if incomplete:
            raise NutritionCalculationError(
                500,
                f"菜谱缺少预计算营养：{', '.join(incomplete)}",
            )
        return [
            _serialize_recipe_nutrition(name, loaded.nutrition_by_name[name])
            for name in validated_names
        ]

    def get_meal_nutrition_targets(
        self,
        profile_id: int,
        meal_period: str,
    ) -> MealNutritionTargetsResult:
        validated_id, validated_meal = validate_profile_and_meal(
            profile_id,
            meal_period,
        )
        try:
            with self._session_factory() as session:
                loaded = self._target_loader(session, validated_id, validated_meal)
        except NutritionCalculationError:
            raise
        except Exception as exc:
            raise NutritionCalculationError(500, "查询用户单餐营养目标失败") from exc

        if loaded.profile is None:
            raise NutritionCalculationError(404, f"用户健康档案不存在：{validated_id}")
        by_nutrient = {row.nutrient: row for row in loaded.targets}
        if (
            set(by_nutrient) != set(NUTRIENT_FIELDS)
            or len(loaded.targets) != len(NUTRIENT_FIELDS)
        ):
            raise NutritionCalculationError(500, "用户单餐营养目标不完整")
        nutrients: dict[str, NutrientTargetResult] = {
            nutrient: _serialize_target(by_nutrient[nutrient])
            for nutrient in NUTRIENT_FIELDS
        }
        return {
            "profile_id": validated_id,
            "meal_period": validated_meal,
            "nutrients": nutrients,
        }


def _serialize_recipe_nutrition(
    name: str,
    value: RecipeNutrition,
) -> RecipeNutritionResult:
    return {
        "recipe_name": name,
        "energy_kcal": value.energy_kcal,
        "protein_g": value.protein_g,
        "fat_g": value.fat_g,
        "carbohydrate_g": value.carbohydrate_g,
        "fiber_g": value.fiber_g,
        "sodium_mg": value.sodium_mg,
        "calcium_mg": value.calcium_mg,
        "iron_mg": value.iron_mg,
        "cholesterol_mg": value.cholesterol_mg,
    }


def _serialize_target(value: ProfileDriTarget) -> NutrientTargetResult:
    return {
        "status": value.status,
        "unit": value.unit,
        "target_value": value.target_value,
        "lower_bound": value.lower_bound,
        "upper_bound": value.upper_bound,
        "target_basis": value.target_basis,
        "lower_basis": value.lower_basis,
        "upper_basis": value.upper_basis,
    }


__all__ = ["NutritionCalculationError", "NutritionService"]
