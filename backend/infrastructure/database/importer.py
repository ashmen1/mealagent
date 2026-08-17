from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from backend.core.nutrition_contract import NUTRIENT_FIELDS
from backend.core.recipe_difficulty import derive_recipe_difficulty

from .models import (
    Ingredient,
    ProfileDriTarget,
    Recipe,
    RecipeIngredient,
    RecipeNutrition,
    UserProfile,
)
from .nutrition_import import (
    NutritionImportConflictError,
    NutritionImportPlan,
    NutritionImportValidationError,
    prepare_nutrition_import,
)


REQUIRED_RECIPE_FIELDS = (
    "name",
    "ingredients",
    "total_time_lower_bound_minutes",
    "atomic_steps",
    "labels",
)

REQUIRED_PROFILE_FIELDS = (
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
)

VALID_SEXES = ("男", "女")
VALID_ACTIVITY_LEVELS = ("低", "中", "高")
VALID_DISH_TYPES = ("菜", "汤", "主食", "小菜", "甜品")
MASS_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)\s*(?:g|克)$", re.IGNORECASE)
GESTATIONAL_WEEK_PATTERN = re.compile(r"^(\d+)周$")


class BasicDataImportError(Exception):
    """基础数据导入错误基类。"""

    status_code = 500


class BasicDataFormatError(BasicDataImportError):
    """输入文件、字段或取值不符合规格。"""

    status_code = 400


class BasicDataConflictError(BasicDataImportError):
    """主键、唯一键或外键发生冲突。"""

    status_code = 409


class BasicDataWriteError(BasicDataImportError):
    """数据库执行写入时失败。"""

    status_code = 500


@dataclass(frozen=True)
class ParsedRecipe:
    name: str
    ingredients: dict[str, str]
    quantity_resolutions: Any
    total_time_lower_bound_minutes: int
    dish_type: str | None
    atomic_steps: list[Any]
    labels: list[Any]


@dataclass(frozen=True)
class ParsedIngredient:
    name: str
    english_name: str | None
    category: str | None
    nutrition: dict[str, Decimal | None]
    aliases: list[str]


@dataclass(frozen=True)
class ParsedProfile:
    id: int
    sex: str
    age: int
    activity_level: str
    special_populations: list[Any]
    gestational_week: int | None
    is_menstruating: bool | None
    taste_preference: str
    allergens: list[Any]
    health_goals: list[Any]
    height_cm: Decimal
    weight_kg: Decimal
    bmi: Decimal
    medical_metrics: dict[str, Any]


@dataclass(frozen=True)
class ParsedBatch:
    recipes: list[ParsedRecipe]
    ingredients: list[ParsedIngredient]
    profiles: list[ParsedProfile]


def import_basic_data(
    recipe_path: str | Path,
    ingredient_path: str | Path,
    profile_path: str | Path,
    dri_path: str | Path,
    session: Session,
) -> dict[str, dict[str, int]]:
    """校验并在同一事务中写入基础数据及营养派生数据。"""

    try:
        batch = _parse_input_files(
            Path(recipe_path),
            Path(ingredient_path),
            Path(profile_path),
        )
        nutrition_plan = prepare_nutrition_import(
            Path(dri_path),
            batch,
        )
    except NutritionImportConflictError as exc:
        session.rollback()
        raise BasicDataConflictError(str(exc)) from exc
    except NutritionImportValidationError as exc:
        session.rollback()
        raise BasicDataFormatError(str(exc)) from exc
    except BasicDataImportError:
        session.rollback()
        raise
    except (OSError, UnicodeError) as exc:
        session.rollback()
        raise BasicDataFormatError(f"基础数据文件读取失败：{exc}") from exc

    try:
        counts = _persist_data(session, batch, nutrition_plan)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise BasicDataConflictError(
            f"基础数据存在主键、唯一键或外键冲突：{exc.orig}"
        ) from exc
    except SQLAlchemyError as exc:
        session.rollback()
        raise BasicDataWriteError(f"基础数据写入失败：{exc.orig}") from exc

    return {"counts": counts}


def _parse_input_files(
    recipe_path: Path,
    ingredient_path: Path,
    profile_path: Path,
) -> ParsedBatch:
    recipes = _parse_recipes(recipe_path)
    ingredients = _parse_ingredients(ingredient_path)
    profiles = _parse_profiles(profile_path)
    _validate_duplicates(recipes, ingredients, profiles)
    return ParsedBatch(
        recipes=recipes,
        ingredients=ingredients,
        profiles=profiles,
    )


def _load_json_array(path: Path, source_name: str) -> list[Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BasicDataFormatError(
            f"{source_name} 不是合法 JSON：第 {exc.lineno} 行第 {exc.colno} 列"
        ) from exc
    if not isinstance(value, list):
        raise BasicDataFormatError(f"{source_name} 顶层必须是数组")
    return value


def _parse_recipes(path: Path) -> list[ParsedRecipe]:
    return [
        _parse_recipe(raw, index)
        for index, raw in enumerate(_load_json_array(path, "RecipeComplete.json"))
    ]


def _parse_recipe(raw: Any, index: int) -> ParsedRecipe:
    location = f"RecipeComplete.json[{index}]"
    recipe = _require_object(raw, location)
    _require_fields(recipe, REQUIRED_RECIPE_FIELDS, location)

    total_time = recipe["total_time_lower_bound_minutes"]
    if not _is_integer(total_time) or total_time < 0:
        raise BasicDataFormatError(
            f"{location}.total_time_lower_bound_minutes 必须是大于等于 0 的整数"
        )

    atomic_steps = _require_list(recipe["atomic_steps"], f"{location}.atomic_steps")
    labels = _require_list(recipe["labels"], f"{location}.labels")
    dish_type = recipe.get("dish_type")
    if dish_type is not None and dish_type not in VALID_DISH_TYPES:
        raise BasicDataFormatError(
            f"{location}.dish_type 必须是菜/汤/主食/小菜/甜品之一"
        )
    return ParsedRecipe(
        name=_require_nonempty_string(recipe["name"], f"{location}.name"),
        ingredients=_parse_recipe_ingredients(recipe["ingredients"], location),
        quantity_resolutions=recipe.get("ingredient_quantity_resolutions"),
        total_time_lower_bound_minutes=total_time,
        dish_type=dish_type,
        atomic_steps=atomic_steps,
        labels=labels,
    )


def _parse_recipe_ingredients(value: Any, recipe_location: str) -> dict[str, str]:
    location = f"{recipe_location}.ingredients"
    ingredients = _require_object(value, location)
    parsed: dict[str, str] = {}
    for ingredient_name, quantity_text in ingredients.items():
        canonical_name = _require_nonempty_string(
            ingredient_name,
            f"{location} 的食材名",
        )
        parsed[canonical_name] = _require_nonempty_string(
            quantity_text,
            f"{location}[{canonical_name}]",
        )
    return parsed


def _parse_ingredients(path: Path) -> list[ParsedIngredient]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None or "标准食材名" not in reader.fieldnames:
                raise BasicDataFormatError(
                    "Ingredients2Nutrition.csv 缺少必需表头：标准食材名"
                )
            return [
                _parse_ingredient_row(raw, line_number)
                for line_number, raw in enumerate(reader, start=2)
            ]
    except csv.Error as exc:
        raise BasicDataFormatError(
            f"Ingredients2Nutrition.csv 格式错误：{exc}"
        ) from exc


def _parse_ingredient_row(
    raw: dict[str, str | None],
    line_number: int,
) -> ParsedIngredient:
    location = f"Ingredients2Nutrition.csv 第 {line_number} 行"
    aliases_text = raw.get("别名") or ""
    return ParsedIngredient(
        name=_require_nonempty_string(
            raw.get("标准食材名"),
            f"{location}.标准食材名",
        ),
        english_name=_empty_to_none(raw.get("英文名")),
        category=_empty_to_none(raw.get("分类")),
        nutrition={
            field: _parse_nullable_decimal(raw.get(field), f"{location}.{field}")
            for field in NUTRIENT_FIELDS
        },
        aliases=[
            alias.strip()
            for alias in aliases_text.split(";")
            if alias.strip()
        ],
    )


def _parse_profiles(path: Path) -> list[ParsedProfile]:
    return [
        _parse_profile(raw, index)
        for index, raw in enumerate(_load_json_array(path, "归一化健康档案 JSON"))
    ]


def _parse_profile(raw: Any, index: int) -> ParsedProfile:
    location = f"归一化健康档案 JSON[{index}]"
    profile = _require_object(raw, location)
    _require_fields(profile, REQUIRED_PROFILE_FIELDS, location)

    profile_id = profile["id"]
    age = profile["年龄"]
    if not _is_integer(profile_id):
        raise BasicDataFormatError(f"{location}.id 必须是整数")
    if not _is_integer(age) or age <= 0:
        raise BasicDataFormatError(f"{location}.年龄 必须是正整数")

    sex = profile["性别"]
    if sex not in VALID_SEXES:
        raise BasicDataFormatError(f"{location}.性别 必须是男或女")
    activity_level = profile["劳动强度"]
    if activity_level not in VALID_ACTIVITY_LEVELS:
        raise BasicDataFormatError(f"{location}.劳动强度 必须是低、中或高")

    special_populations = _require_list(
        profile["特殊人群"],
        f"{location}.特殊人群",
    )
    medical_metrics = _require_object(profile["体检指标"], f"{location}.体检指标")
    return ParsedProfile(
        id=profile_id,
        sex=sex,
        age=age,
        activity_level=activity_level,
        special_populations=special_populations,
        gestational_week=_parse_gestational_week(
            profile.get("孕周期"),
            "孕妇" in special_populations,
            f"{location}.孕周期",
        ),
        is_menstruating=_parse_is_menstruating(
            profile.get("是否有月经"),
            f"{location}.是否有月经",
        ),
        taste_preference=_require_nonempty_string(
            profile["口味偏好"],
            f"{location}.口味偏好",
        ),
        allergens=_require_list(profile["过敏食材"], f"{location}.过敏食材"),
        health_goals=_require_list(profile["健康需求"], f"{location}.健康需求"),
        height_cm=_parse_positive_decimal(
            profile["身高_cm"],
            f"{location}.身高_cm",
        ),
        weight_kg=_parse_positive_decimal(
            profile["体重_kg"],
            f"{location}.体重_kg",
        ),
        bmi=_parse_positive_decimal(profile["BMI"], f"{location}.BMI"),
        medical_metrics=medical_metrics,
    )


def _validate_duplicates(
    recipes: list[ParsedRecipe],
    ingredients: list[ParsedIngredient],
    profiles: list[ParsedProfile],
) -> None:
    _reject_duplicates([recipe.name for recipe in recipes], "菜名")
    _reject_duplicates(
        [ingredient.name for ingredient in ingredients],
        "归一化食材名",
    )
    _reject_duplicates([profile.id for profile in profiles], "用户 ID")


def _persist_data(
    session: Session,
    batch: ParsedBatch,
    nutrition_plan: NutritionImportPlan,
) -> dict[str, int]:
    ingredient_models = _build_ingredient_models(batch.ingredients)
    recipe_models = _build_recipe_models(batch.recipes)
    session.add_all([*ingredient_models.values(), *recipe_models.values()])
    session.flush()

    associations = _build_associations(
        batch.recipes,
        recipe_models,
        ingredient_models,
        nutrition_plan,
    )
    profile_models = _build_profile_models(batch.profiles)
    session.add_all([*associations, *profile_models])
    session.flush()

    recipe_nutrition_models = [
        RecipeNutrition(
            recipe_id=recipe_models[recipe_name].id,
            **nutrition,
        )
        for recipe_name, nutrition in nutrition_plan.recipe_nutrition.items()
    ]
    profile_target_models = [
        ProfileDriTarget(**target)
        for target in nutrition_plan.profile_targets
    ]
    session.add_all([*recipe_nutrition_models, *profile_target_models])
    session.flush()

    return {
        "recipes": len(recipe_models),
        "ingredients": len(ingredient_models),
        "recipe_ingredients": len(associations),
        "user_profiles": len(profile_models),
        "recipe_nutrition": len(recipe_nutrition_models),
        "profile_dri_targets": len(profile_target_models),
    }


def _build_ingredient_models(
    nutrition_ingredients: list[ParsedIngredient],
) -> dict[str, Ingredient]:
    # prepare_nutrition_import 已严格校验所有实际使用食材均存在营养记录。
    # 此处只负责把已解析的静态营养数据转换为数据库模型，不再补建空食材。
    return {
        item.name: Ingredient(
            name=item.name,
            english_name=item.english_name,
            category=item.category,
            aliases=item.aliases,
            **item.nutrition,
        )
        for item in nutrition_ingredients
    }


def _build_recipe_models(recipes: list[ParsedRecipe]) -> dict[str, Recipe]:
    return {
        item.name: Recipe(
            name=item.name,
            total_time_lower_bound_minutes=item.total_time_lower_bound_minutes,
            dish_type=item.dish_type,
            atomic_steps=item.atomic_steps,
            labels=item.labels,
            difficulty=derive_recipe_difficulty(
                total_time_minutes=item.total_time_lower_bound_minutes,
                atomic_step_count=len(item.atomic_steps),
                ingredient_count=len(item.ingredients),
            ),
        )
        for item in recipes
    }


def _build_associations(
    recipes: list[ParsedRecipe],
    recipe_models: dict[str, Recipe],
    ingredient_models: dict[str, Ingredient],
    nutrition_plan: NutritionImportPlan,
) -> list[RecipeIngredient]:
    associations: list[RecipeIngredient] = []
    for recipe in recipes:
        for ingredient_name, quantity_text in recipe.ingredients.items():
            resolved = nutrition_plan.resolved_quantities[
                (recipe.name, ingredient_name)
            ]
            associations.append(
                RecipeIngredient(
                    recipe_id=recipe_models[recipe.name].id,
                    ingredient_id=ingredient_models[ingredient_name].id,
                    quantity_text=quantity_text,
                    quantity_g=_parse_quantity_g(quantity_text),
                    resolved_quantity_g=resolved.grams,
                    is_quantity_estimated=resolved.is_estimated,
                    is_nutrition_excluded=resolved.is_nutrition_excluded,
                )
            )
    return associations


def _build_profile_models(profiles: list[ParsedProfile]) -> list[UserProfile]:
    return [
        UserProfile(
            id=item.id,
            sex=item.sex,
            age=item.age,
            activity_level=item.activity_level,
            special_populations=item.special_populations,
            gestational_week=item.gestational_week,
            is_menstruating=item.is_menstruating,
            taste_preference=item.taste_preference,
            allergens=item.allergens,
            health_goals=item.health_goals,
            height_cm=item.height_cm,
            weight_kg=item.weight_kg,
            bmi=item.bmi,
            medical_metrics=item.medical_metrics,
        )
        for item in profiles
    ]


def _require_object(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BasicDataFormatError(f"{location} 必须是对象")
    return value


def _require_fields(
    raw: dict[str, Any],
    fields: tuple[str, ...],
    location: str,
) -> None:
    missing = [field for field in fields if field not in raw]
    if missing:
        raise BasicDataFormatError(
            f"{location} 缺少必填字段：{', '.join(missing)}"
        )


def _require_nonempty_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or value == "":
        raise BasicDataFormatError(f"{location} 必须是非空字符串")
    return value


def _require_list(value: Any, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise BasicDataFormatError(f"{location} 必须是数组")
    return value


def _parse_nullable_decimal(value: Any, location: str) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BasicDataFormatError(f"{location} 必须是十进制数或空值") from exc


def _parse_positive_decimal(value: Any, location: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise BasicDataFormatError(f"{location} 必须是大于 0 的数字")
    decimal_value = Decimal(str(value))
    if decimal_value <= 0:
        raise BasicDataFormatError(f"{location} 必须大于 0")
    return decimal_value


def _parse_gestational_week(
    value: Any,
    is_pregnant: bool,
    location: str,
) -> int | None:
    if not is_pregnant:
        if value is not None:
            raise BasicDataFormatError(f"{location} 非孕妇必须为 null")
        return None
    if _is_integer(value) and value > 0:
        return value
    if isinstance(value, str):
        match = GESTATIONAL_WEEK_PATTERN.fullmatch(value)
        if match and int(match.group(1)) > 0:
            return int(match.group(1))
    raise BasicDataFormatError(f"{location} 孕妇必须填写正整数孕周")


def _parse_is_menstruating(value: Any, location: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise BasicDataFormatError(f"{location} 必须为布尔值或 null")
    return value


def _parse_quantity_g(quantity_text: str) -> Decimal | None:
    match = MASS_PATTERN.fullmatch(quantity_text)
    if match is None:
        return None
    return Decimal(match.group(1))


def _reject_duplicates(values: list[Any], field_name: str) -> None:
    seen: set[Any] = set()
    for value in values:
        if value in seen:
            raise BasicDataConflictError(f"{field_name}重复：{value}")
        seen.add(value)


def _empty_to_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
