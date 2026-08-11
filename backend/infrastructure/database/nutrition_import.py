from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from backend.core.nutrition_contract import NUTRIENT_FIELDS

MEAL_FACTORS = {
    "早餐": Decimal("0.30"),
    "午餐": Decimal("0.40"),
    "晚餐": Decimal("0.30"),
}
TWO_PLACES = Decimal("0.01")
MJ_TO_KCAL = Decimal("239")

DRI_FIELDS = (
    "性别",
    "年龄下限",
    "年龄上限",
    "生理阶段",
    "劳动强度",
    "energy_mj",
    "protein_rni_g",
    "protein_amdr_min_percent",
    "protein_amdr_max_percent",
    "fat_amdr_min_percent",
    "fat_amdr_max_percent",
    "carbohydrate_amdr_min_percent",
    "carbohydrate_amdr_max_percent",
    "fiber_ai_min_g",
    "fiber_ai_max_g",
    "sodium_ai_mg",
    "sodium_pi_mg",
    "calcium_rni_mg",
    "calcium_ul_mg",
    "iron_rni_mg",
    "iron_ul_mg",
)

NUTRITION_EXCLUDED_WATER_NAMES = frozenset(
    {
        "水",
        "清水",
        "温水",
        "温开水",
        "热水",
        "凉开水",
        "冰水",
        "冷水",
        "纯净水",
        "开水",
    }
)


class NutritionImportValidationError(ValueError):
    """营养静态数据或业务输入不符合规格。"""


class NutritionImportConflictError(ValueError):
    """营养静态数据存在重复键。"""


@dataclass(frozen=True)
class ResolvedQuantity:
    grams: Decimal
    is_estimated: bool
    is_nutrition_excluded: bool = False


@dataclass(frozen=True)
class DriRule:
    sex: str
    age_min: int
    age_max: int | None
    life_stage: str
    activity_level: str
    energy_mj: Decimal
    protein_rni_g: Decimal
    protein_amdr_min_percent: Decimal
    protein_amdr_max_percent: Decimal
    fat_amdr_min_percent: Decimal
    fat_amdr_max_percent: Decimal
    carbohydrate_amdr_min_percent: Decimal
    carbohydrate_amdr_max_percent: Decimal
    fiber_ai_min_g: Decimal
    fiber_ai_max_g: Decimal
    sodium_ai_mg: Decimal
    sodium_pi_mg: Decimal
    calcium_rni_mg: Decimal
    calcium_ul_mg: Decimal
    iron_rni_mg: Decimal
    iron_ul_mg: Decimal


@dataclass(frozen=True)
class DriTargetSpec:
    nutrient: str
    unit: str
    target_value: Decimal | None
    lower_bound: Decimal | None
    upper_bound: Decimal | None
    target_basis: str | None
    lower_basis: str | None
    upper_basis: str | None


@dataclass(frozen=True)
class NutritionImportPlan:
    resolved_quantities: dict[tuple[str, str], ResolvedQuantity]
    recipe_nutrition: dict[str, dict[str, Decimal]]
    profile_targets: list[dict[str, Any]]


def prepare_nutrition_import(
    dri_path: Path,
    batch: Any,
) -> NutritionImportPlan:
    """在写库前完成全部克重、营养和DRI计算。"""

    dri_rules = _parse_dri_rules(dri_path)
    resolved = _load_recipe_quantity_resolutions(batch.recipes)

    ingredients_by_name = {item.name: item for item in batch.ingredients}
    _validate_used_ingredients(batch.recipes, ingredients_by_name)

    recipe_nutrition = _calculate_recipe_nutrition(
        batch.recipes,
        ingredients_by_name,
        resolved,
    )
    profile_targets = _calculate_profile_targets(batch.profiles, dri_rules)
    return NutritionImportPlan(resolved, recipe_nutrition, profile_targets)


def _load_recipe_quantity_resolutions(
    recipes: list[Any],
) -> dict[tuple[str, str], ResolvedQuantity]:
    """读取菜谱内嵌的最终克重及证据，确保与食材关联一一对应。"""

    resolved: dict[tuple[str, str], ResolvedQuantity] = {}
    for recipe in recipes:
        resolution_map = _require_resolution_map(recipe)
        for ingredient_name, quantity_text in recipe.ingredients.items():
            resolved[(recipe.name, ingredient_name)] = _parse_quantity_resolution(
                recipe.name,
                ingredient_name,
                quantity_text,
                resolution_map[ingredient_name],
            )
    return resolved


def _require_resolution_map(recipe: Any) -> dict[str, Any]:
    resolution_map = recipe.quantity_resolutions
    if not isinstance(resolution_map, dict):
        raise NutritionImportValidationError(
            f"菜谱 {recipe.name}.ingredient_quantity_resolutions 必须是对象"
        )
    ingredient_names = set(recipe.ingredients)
    resolution_names = set(resolution_map)
    if ingredient_names != resolution_names:
        missing = sorted(ingredient_names - resolution_names)
        extra = sorted(resolution_names - ingredient_names)
        raise NutritionImportValidationError(
            f"菜谱 {recipe.name} 最终克重必须与食材一一对应："
            f"缺少={missing}，额外={extra}"
        )
    return resolution_map


def _parse_quantity_resolution(
    recipe_name: str,
    ingredient_name: str,
    quantity_text: str,
    item: Any,
) -> ResolvedQuantity:
    location = f"菜谱 {recipe_name}/{ingredient_name}"
    if not isinstance(item, dict):
        raise NutritionImportValidationError(f"{location} 最终克重记录必须是对象")

    original_quantity = _required_text(
        item.get("original_quantity"),
        f"{location}.original_quantity",
    )
    if original_quantity != quantity_text:
        raise NutritionImportValidationError(
            f"{location} 原始数量与最终克重记录不一致："
            f"ingredients={quantity_text}，resolution={original_quantity}"
        )

    is_estimated = _required_bool(
        item.get("is_quantity_estimated"),
        f"{location}.is_quantity_estimated",
    )
    is_excluded = _required_bool(
        item.get("is_nutrition_excluded"),
        f"{location}.is_nutrition_excluded",
    )
    grams = _non_negative_decimal(
        item.get("resolved_quantity_g"),
        f"{location}.resolved_quantity_g",
    )
    _validate_resolved_grams(
        ingredient_name,
        grams,
        is_estimated,
        is_excluded,
        location,
    )
    _required_text(item.get("calculation_path"), f"{location}.calculation_path")
    _required_text(item.get("reference_source"), f"{location}.reference_source")
    _validate_weight_distribution(
        item.get("ingredient_weight_distribution"),
        location,
        is_excluded,
    )
    return ResolvedQuantity(_round(grams), is_estimated, is_excluded)


def _validate_resolved_grams(
    ingredient_name: str,
    grams: Decimal,
    is_estimated: bool,
    is_excluded: bool,
    location: str,
) -> None:
    if not is_excluded:
        if grams <= 0:
            raise NutritionImportValidationError(f"{location} 最终克重必须大于0")
        return
    if ingredient_name not in NUTRITION_EXCLUDED_WATER_NAMES:
        raise NutritionImportValidationError(f"{location} 仅纯水允许标记为营养排除")
    if grams != 0:
        raise NutritionImportValidationError(f"{location} 营养排除项克重必须为0")
    if is_estimated:
        raise NutritionImportValidationError(f"{location} 营养排除项不得标记为估算")


def _validate_weight_distribution(
    value: Any,
    location: str,
    is_excluded: bool,
) -> None:
    if not isinstance(value, dict):
        raise NutritionImportValidationError(
            f"{location}.ingredient_weight_distribution 必须是对象"
        )
    method = _required_text(value.get("method"), f"{location}.distribution.method")
    sample_count = value.get("sample_count")
    if not isinstance(sample_count, int) or isinstance(sample_count, bool) or sample_count < 0:
        raise NutritionImportValidationError(
            f"{location}.distribution.sample_count 必须为非负整数"
        )
    if is_excluded:
        if sample_count != 0 or method != "nutrition_excluded":
            raise NutritionImportValidationError(
                f"{location} 营养排除分布必须为0样本且方法为nutrition_excluded"
            )
        return
    if sample_count <= 0:
        raise NutritionImportValidationError(
            f"{location}.distribution.sample_count 必须大于0"
        )
    ordered_fields = ("min_g", "p25_g", "median_g", "p75_g", "max_g")
    ordered = [
        _positive_decimal(value.get(field), f"{location}.distribution.{field}")
        for field in ordered_fields
    ]
    if ordered != sorted(ordered):
        raise NutritionImportValidationError(f"{location} 克重分布分位值顺序非法")
    _positive_decimal(value.get("mean_g"), f"{location}.distribution.mean_g")
    common_values = value.get("common_values")
    if not isinstance(common_values, list) or not common_values:
        raise NutritionImportValidationError(
            f"{location}.distribution.common_values 必须是非空数组"
        )
    for index, common in enumerate(common_values, start=1):
        if not isinstance(common, dict):
            raise NutritionImportValidationError(
                f"{location}.distribution.common_values[{index}] 必须是对象"
            )
        _positive_decimal(
            common.get("grams"),
            f"{location}.distribution.common_values[{index}].grams",
        )
        _positive_int(
            common.get("count"),
            f"{location}.distribution.common_values[{index}].count",
        )


def _read_csv(
    path: Path,
    required_fields: tuple[str, ...],
    source: str,
) -> list[dict[str, str | None]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            fields = set(reader.fieldnames or ())
            missing = [field for field in required_fields if field not in fields]
            if missing:
                raise NutritionImportValidationError(
                    f"{source} 缺少必需表头：{', '.join(missing)}"
                )
            return list(reader)
    except csv.Error as exc:
        raise NutritionImportValidationError(f"{source} 格式错误：{exc}") from exc


def _parse_dri_rules(path: Path) -> list[DriRule]:
    rows = _read_csv(path, DRI_FIELDS, "DRI2023.csv")
    result: list[DriRule] = []
    seen: set[tuple[str, int, int | None, str, str]] = set()
    decimal_fields = DRI_FIELDS[5:]
    for line_number, row in enumerate(rows, start=2):
        location = f"DRI2023.csv 第{line_number}行"
        sex = _required_text(row.get("性别"), f"{location}.性别")
        life_stage = _required_text(row.get("生理阶段"), f"{location}.生理阶段")
        activity = _required_text(row.get("劳动强度"), f"{location}.劳动强度")
        if sex not in ("男", "女"):
            raise NutritionImportValidationError(f"{location}.性别 必须为男或女")
        if life_stage not in ("普通", "无月经", "孕早期", "孕中期", "孕晚期", "哺乳期"):
            raise NutritionImportValidationError(f"{location}.生理阶段非法")
        if activity not in ("低", "中", "高"):
            raise NutritionImportValidationError(f"{location}.劳动强度非法")
        age_min = _positive_int(row.get("年龄下限"), f"{location}.年龄下限")
        age_max = _nullable_positive_int(row.get("年龄上限"), f"{location}.年龄上限")
        if age_max is not None and age_max < age_min:
            raise NutritionImportValidationError(f"{location}.年龄范围倒置")
        values = {
            field: _positive_decimal(row.get(field), f"{location}.{field}")
            for field in decimal_fields
        }
        if values["protein_amdr_min_percent"] > values["protein_amdr_max_percent"]:
            raise NutritionImportValidationError(f"{location}.蛋白质AMDR范围倒置")
        if values["fat_amdr_min_percent"] > values["fat_amdr_max_percent"]:
            raise NutritionImportValidationError(f"{location}.脂肪AMDR范围倒置")
        if values["carbohydrate_amdr_min_percent"] > values["carbohydrate_amdr_max_percent"]:
            raise NutritionImportValidationError(f"{location}.碳水AMDR范围倒置")
        key = (sex, age_min, age_max, life_stage, activity)
        if key in seen:
            raise NutritionImportConflictError(f"DRI规则重复：{key}")
        seen.add(key)
        result.append(
            DriRule(
                sex=sex,
                age_min=age_min,
                age_max=age_max,
                life_stage=life_stage,
                activity_level=activity,
                **values,
            )
        )
    return result


def _validate_used_ingredients(recipes: list[Any], ingredients: dict[str, Any]) -> None:
    for recipe in recipes:
        for ingredient_name in recipe.ingredients:
            ingredient = ingredients.get(ingredient_name)
            if ingredient is None:
                raise NutritionImportValidationError(
                    f"菜谱 {recipe.name} 使用的食材缺少营养记录：{ingredient_name}"
                )
            for field in NUTRIENT_FIELDS:
                value = ingredient.nutrition.get(field)
                if value is None:
                    raise NutritionImportValidationError(
                        f"菜谱 {recipe.name} 的食材 {ingredient_name} 缺少营养：{field}"
                    )
                if value < 0:
                    raise NutritionImportValidationError(
                        f"食材 {ingredient_name}.{field} 不得为负数"
                    )


def _calculate_recipe_nutrition(
    recipes: list[Any],
    ingredients: dict[str, Any],
    resolved: dict[tuple[str, str], ResolvedQuantity],
) -> dict[str, dict[str, Decimal]]:
    result: dict[str, dict[str, Decimal]] = {}
    for recipe in recipes:
        totals = {field: Decimal("0") for field in NUTRIENT_FIELDS}
        for ingredient_name in recipe.ingredients:
            grams = resolved[(recipe.name, ingredient_name)].grams
            nutrition = ingredients[ingredient_name].nutrition
            for field in NUTRIENT_FIELDS:
                totals[field] += nutrition[field] * grams / 100
        result[recipe.name] = {field: _round(value) for field, value in totals.items()}
    return result


def _calculate_profile_targets(
    profiles: list[Any],
    rules: list[DriRule],
) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for profile in profiles:
        life_stage = _validate_profile_and_get_stage(profile)
        rule = _find_dri_rule(profile, life_stage, rules)
        for meal_period, factor in MEAL_FACTORS.items():
            targets.extend(_targets_for_rule(profile.id, meal_period, factor, rule))
    return targets


def _find_dri_rule(
    profile: Any,
    life_stage: str,
    rules: list[DriRule],
) -> DriRule:
    matches = [
        rule
        for rule in rules
        if rule.sex == profile.sex
        and rule.activity_level == profile.activity_level
        and rule.life_stage == life_stage
        and rule.age_min <= profile.age
        and (rule.age_max is None or profile.age <= rule.age_max)
    ]
    if len(matches) != 1:
        raise NutritionImportValidationError(
            f"用户 {profile.id} 必须唯一匹配DRI规则，实际匹配 {len(matches)} 条"
        )
    return matches[0]


def _validate_profile_and_get_stage(profile: Any) -> str:
    is_pregnant = "孕妇" in profile.special_populations
    is_lactating = "哺乳期" in profile.special_populations
    if profile.age < 18:
        raise NutritionImportValidationError(f"用户 {profile.id} 年龄不在成人DRI范围")
    if profile.age >= 65 and profile.activity_level == "高":
        raise NutritionImportValidationError(f"用户 {profile.id} 的老年高劳动强度没有官方EER")
    if is_pregnant and is_lactating:
        raise NutritionImportValidationError(f"用户 {profile.id} 不能同时为孕妇和哺乳期")
    if is_pregnant:
        if profile.sex != "女" or not 18 <= profile.age <= 49:
            raise NutritionImportValidationError(f"用户 {profile.id} 的妊娠身份不合法")
        week = profile.gestational_week
        if week is None or not 1 <= week <= 42:
            raise NutritionImportValidationError(f"用户 {profile.id} 的孕周必须为1至42")
        if week <= 12:
            return "孕早期"
        if week <= 27:
            return "孕中期"
        return "孕晚期"
    if is_lactating:
        if profile.sex != "女" or not 18 <= profile.age <= 49:
            raise NutritionImportValidationError(f"用户 {profile.id} 的哺乳期身份不合法")
        return "哺乳期"
    if profile.sex == "女" and 50 <= profile.age <= 64:
        if profile.is_menstruating is None:
            raise NutritionImportValidationError(f"用户 {profile.id} 必须填写是否有月经")
        return "普通" if profile.is_menstruating else "无月经"
    if profile.is_menstruating is not None:
        raise NutritionImportValidationError(f"用户 {profile.id} 不应填写月经状态")
    return "普通"


def _targets_for_rule(
    profile_id: int,
    meal_period: str,
    factor: Decimal,
    rule: DriRule,
) -> list[dict[str, Any]]:
    energy_kcal = rule.energy_mj * MJ_TO_KCAL

    protein_low = energy_kcal * rule.protein_amdr_min_percent / 100 / 4
    protein_high = energy_kcal * rule.protein_amdr_max_percent / 100 / 4
    fat_low = energy_kcal * rule.fat_amdr_min_percent / 100 / 9
    fat_high = energy_kcal * rule.fat_amdr_max_percent / 100 / 9
    carbohydrate_low = energy_kcal * rule.carbohydrate_amdr_min_percent / 100 / 4
    carbohydrate_high = energy_kcal * rule.carbohydrate_amdr_max_percent / 100 / 4
    target_specs = [
        DriTargetSpec(
            "energy_kcal", "kcal", energy_kcal, None, None, "EER", None, None
        ),
        DriTargetSpec(
            "protein_g",
            "g",
            rule.protein_rni_g,
            protein_low,
            protein_high,
            "RNI",
            "AMDR",
            "AMDR",
        ),
        DriTargetSpec(
            "fat_g", "g", None, fat_low, fat_high, None, "AMDR", "AMDR"
        ),
        DriTargetSpec(
            "carbohydrate_g",
            "g",
            None,
            carbohydrate_low,
            carbohydrate_high,
            None,
            "AMDR",
            "AMDR",
        ),
        DriTargetSpec(
            "fiber_g",
            "g",
            None,
            rule.fiber_ai_min_g,
            rule.fiber_ai_max_g,
            None,
            "AI",
            "AI",
        ),
        DriTargetSpec(
            "sodium_mg",
            "mg",
            rule.sodium_ai_mg,
            None,
            rule.sodium_pi_mg,
            "AI",
            None,
            "PI",
        ),
        DriTargetSpec(
            "calcium_mg",
            "mg",
            rule.calcium_rni_mg,
            None,
            rule.calcium_ul_mg,
            "RNI",
            None,
            "UL",
        ),
        DriTargetSpec(
            "iron_mg",
            "mg",
            rule.iron_rni_mg,
            None,
            rule.iron_ul_mg,
            "RNI",
            None,
            "UL",
        ),
    ]
    rows = [
        _build_available_target(profile_id, meal_period, factor, spec)
        for spec in target_specs
    ]
    rows.append(_build_cholesterol_target(profile_id, meal_period))
    return rows


def _build_available_target(
    profile_id: int,
    meal_period: str,
    factor: Decimal,
    spec: DriTargetSpec,
) -> dict[str, Any]:
    return {
        "profile_id": profile_id,
        "meal_period": meal_period,
        "nutrient": spec.nutrient,
        "status": "available",
        "unit": spec.unit,
        "target_value": _scale_target(spec.target_value, factor),
        "lower_bound": _scale_target(spec.lower_bound, factor),
        "upper_bound": _scale_target(spec.upper_bound, factor),
        "target_basis": spec.target_basis,
        "lower_basis": spec.lower_basis,
        "upper_basis": spec.upper_basis,
    }


def _build_cholesterol_target(
    profile_id: int,
    meal_period: str,
) -> dict[str, Any]:
    return {
        "profile_id": profile_id,
        "meal_period": meal_period,
        "nutrient": "cholesterol_mg",
        "status": "not_established",
        "unit": "mg",
        "target_value": None,
        "lower_bound": None,
        "upper_bound": None,
        "target_basis": None,
        "lower_basis": None,
        "upper_basis": None,
    }


def _scale_target(value: Decimal | None, factor: Decimal) -> Decimal | None:
    return _round(value * factor) if value is not None else None


def _round(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _required_text(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NutritionImportValidationError(f"{location} 必须是非空字符串")
    return value.strip()


def _required_bool(value: Any, location: str) -> bool:
    if not isinstance(value, bool):
        raise NutritionImportValidationError(f"{location} 必须是布尔值")
    return value


def _non_negative_decimal(value: Any, location: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise NutritionImportValidationError(f"{location} 必须是十进制数") from exc
    if not result.is_finite() or result < 0:
        raise NutritionImportValidationError(f"{location} 不得小于0")
    return result


def _positive_decimal(value: Any, location: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise NutritionImportValidationError(f"{location} 必须是十进制数") from exc
    if not result.is_finite() or result <= 0:
        raise NutritionImportValidationError(f"{location} 必须大于0")
    return result


def _positive_int(value: Any, location: str) -> int:
    try:
        result = int(str(value))
    except ValueError as exc:
        raise NutritionImportValidationError(f"{location} 必须是正整数") from exc
    if str(result) != str(value).strip() or result <= 0:
        raise NutritionImportValidationError(f"{location} 必须是正整数")
    return result


def _nullable_positive_int(value: Any, location: str) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return _positive_int(value, location)


__all__ = [
    "NutritionImportConflictError",
    "NutritionImportPlan",
    "NutritionImportValidationError",
    "prepare_nutrition_import",
]
