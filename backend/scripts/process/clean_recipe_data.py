from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Final


REPOSITORY_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
RECIPE_PATH: Final[Path] = (
    REPOSITORY_ROOT / "datas" / "processed" / "Recipes" / "RecipeComplete.json"
)
INGREDIENT_PATH: Final[Path] = (
    REPOSITORY_ROOT
    / "datas"
    / "processed"
    / "Ingredients"
    / "Ingredients2Nutrition.csv"
)

EXPECTED_RECIPE_COUNT: Final[int] = 1913
EXPECTED_USED_INGREDIENT_COUNT: Final[int] = 1239
EXPECTED_INGREDIENT_OCCURRENCE_COUNT: Final[int] = 16263
EXPECTED_ATOMIC_STEP_COUNT: Final[int] = 17884
EXPECTED_LABEL_COUNT: Final[int] = 159
EXPECTED_LABEL_OCCURRENCE_COUNT: Final[int] = 14573

INGREDIENT_NAME_MAPPING: Final[dict[str, str]] = {
    "红椒": "甜椒",
    "红彩椒": "甜椒",
    "青红椒": "甜椒",
    "绿彩椒": "甜椒",
    "圆椒": "甜椒",
    "红甜椒": "甜椒",
    "红尖椒": "大红尖椒",
    "青红辣椒": "辣椒",
    "豆干": "豆腐干",
    "内酯": "葡萄糖酸内酯",
    "六月黄母蟹": "大闸蟹",
    "水晶柿子": "柿子",
    "炼奶": "炼乳",
    "菠菜粉/或菠菜汁": "菠菜",
    "黑椒汁": "黑胡椒汁",
    "开水": "热水",
    "沸水": "热水",
    "温热水": "温水",
    "海盐": "盐",
    "粗砂糖": "糖",
    "细砂糖（蛋白打发）": "细砂糖",
}

REMOVED_OPERATION_MATERIALS: Final[frozenset[str]] = frozenset(
    {
        "粽叶",
        "棉线",
        "棉绳",
        "牙签",
        "盐焗鸡竹笋纸",
    }
)

REMOVED_RECIPE_NAMES: Final[frozenset[str]] = frozenset(
    {
        "乳化",
        "颠勺",
        "酸奶发酵",
        "高温快煮",
        "68℃慢煮",
        "59℃慢煮",
        "停刀烧煮",
    }
)

PLACEHOLDER_INGREDIENT_MAPPING: Final[dict[str, dict[str, str]]] = {
    "芝士牛肉卷": {"主料": "牛肉卷"},
    "叉烧包": {"主料": "叉烧包"},
    "速冻馒头": {"食材1": "馒头"},
}

RESTORED_LOBSTER_INGREDIENTS: Final[dict[str, str]] = {
    "水": "1000g",
    "小龙虾": "500g",
    "食用油": "80g",
    "大蒜": "150g",
    "姜": "15g",
    "葱白": "30g",
    "黄灯笼辣椒酱": "20g",
    "鸡汁": "20g",
    "蒜粉": "2大勺",
    "盐": "3小勺",
    "白糖": "2大勺",
    "白胡椒粉": "2小勺",
    "啤酒": "200g",
    "香菜": "1根",
}

ADDED_INGREDIENT_ROWS: Final[dict[str, dict[str, str]]] = {
    "葡萄糖酸内酯": {
        "标准食材名": "葡萄糖酸内酯",
        "英文名": "",
        "分类": "调料",
        "别名": "内酯",
    },
    "柿子": {
        "标准食材名": "柿子",
        "英文名": "",
        "分类": "水果",
        "别名": "水晶柿子",
    },
    "叉烧包": {
        "标准食材名": "叉烧包",
        "英文名": "",
        "分类": "粮食",
        "别名": "",
    },
}

AUXILIARY_INGREDIENT_NAMES: Final[frozenset[str]] = frozenset(
    {
        "espresso",
        "乌龙茶",
        "伯爵红茶",
        "可乐",
        "咖啡粉",
        "咖啡豆",
        "啤酒",
        "大葱",
        "大蒜",
        "姜",
        "小葱",
        "无酒精饮料",
        "朗姆酒",
        "果蔬汁",
        "桃汁",
        "椰子水",
        "樱桃酒",
        "橄榄油",
        "橙汁",
        "气泡水",
        "水",
        "波特酒",
        "淀粉",
        "清水",
        "清酒",
        "温开水",
        "温水",
        "热水",
        "猪油",
        "玫瑰露酒",
        "甜酒酿",
        "生姜",
        "生粉",
        "白兰地",
        "白糖",
        "白葡萄酒",
        "米酒",
        "红茶",
        "红茶包",
        "红葡萄酒",
        "红薯淀粉",
        "红酒",
        "绍酒",
        "绿茶",
        "胡萝卜汁",
        "花雕酒",
        "苏打水",
        "苹果酒",
        "茉莉花茶",
        "茶叶",
        "菜籽油",
        "菠萝醋",
        "葡萄酒",
        "葱",
        "蒜",
        "蒜末",
        "辣椒",
        "青麦汁",
        "面粉",
        "食用油",
        "黑啤酒",
        "龙井茶叶",
    }
)


class RecipeDataCleaningError(Exception):
    """菜品与食材数据不符合清洗后的约束。"""


def load_recipes() -> list[dict[str, Any]]:
    """读取最终菜品数据。"""

    value = json.loads(RECIPE_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise RecipeDataCleaningError("RecipeComplete.json 顶层必须是数组")
    return value


def load_ingredient_rows() -> tuple[list[str], list[dict[str, str]]]:
    """读取标准食材数据并保留原表头顺序。"""

    with INGREDIENT_PATH.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise RecipeDataCleaningError("Ingredients2Nutrition.csv 缺少表头")
        return list(reader.fieldnames), [dict(row) for row in reader]


def clean_recipes(recipes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """应用已审定的菜品删除、食材恢复和食材名归一规则。"""

    cleaned: list[dict[str, Any]] = []
    for recipe in recipes:
        recipe_name = recipe.get("name")
        if recipe_name in REMOVED_RECIPE_NAMES:
            continue

        cleaned_recipe = dict(recipe)
        ingredients = recipe.get("ingredients")
        if not isinstance(ingredients, dict):
            raise RecipeDataCleaningError(f"{recipe_name} 的 ingredients 必须是对象")

        if recipe_name == "金汤蒜蓉小龙虾":
            cleaned_recipe["ingredients"] = dict(RESTORED_LOBSTER_INGREDIENTS)
        else:
            cleaned_recipe["ingredients"] = _normalize_ingredients(
                recipe_name,
                ingredients,
            )

        if "fuzzy_quantity_estimates" in cleaned_recipe:
            cleaned_recipe["fuzzy_quantity_estimates"] = _clean_embedded_estimates(
                recipe_name,
                cleaned_recipe["fuzzy_quantity_estimates"],
            )
        cleaned.append(cleaned_recipe)
    return cleaned


def _normalize_ingredients(
    recipe_name: object,
    ingredients: dict[str, Any],
) -> dict[str, str]:
    local_mapping = dict(INGREDIENT_NAME_MAPPING)
    if isinstance(recipe_name, str):
        local_mapping.update(PLACEHOLDER_INGREDIENT_MAPPING.get(recipe_name, {}))

    normalized: dict[str, str] = {}
    for ingredient_name, quantity_text in ingredients.items():
        if not isinstance(ingredient_name, str) or not isinstance(quantity_text, str):
            raise RecipeDataCleaningError(
                f"{recipe_name} 的食材名和数量必须是字符串"
            )
        if ingredient_name in REMOVED_OPERATION_MATERIALS:
            continue

        normalized_name = local_mapping.get(ingredient_name, ingredient_name)
        if normalized_name in normalized:
            normalized[normalized_name] = (
                f"{normalized[normalized_name]}; {quantity_text}"
            )
        else:
            normalized[normalized_name] = quantity_text
    return normalized


def _clean_embedded_estimates(
    recipe_name: object,
    estimates: object,
) -> list[dict[str, Any]]:
    if not isinstance(estimates, list):
        raise RecipeDataCleaningError(
            f"{recipe_name} 的 fuzzy_quantity_estimates 必须是数组"
        )

    cleaned: list[dict[str, Any]] = []
    for estimate in estimates:
        if not isinstance(estimate, dict):
            raise RecipeDataCleaningError(
                f"{recipe_name} 的 fuzzy_quantity_estimates 元素必须是对象"
            )
        canonical_name = estimate.get("ingredient_canonical")
        if canonical_name in REMOVED_OPERATION_MATERIALS:
            continue
        cleaned_estimate = dict(estimate)
        if isinstance(canonical_name, str):
            cleaned_estimate["ingredient_canonical"] = INGREDIENT_NAME_MAPPING.get(
                canonical_name,
                canonical_name,
            )
        cleaned.append(cleaned_estimate)
    return cleaned


def ensure_added_ingredient_rows(
    fieldnames: list[str],
    rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    """补充清洗后新增的三个标准食材。"""

    if "标准食材名" not in fieldnames or "分类" not in fieldnames:
        raise RecipeDataCleaningError("食材 CSV 缺少标准食材名或分类表头")

    rows_by_name = {row.get("标准食材名", ""): row for row in rows}
    for ingredient_name, required_values in ADDED_INGREDIENT_ROWS.items():
        existing = rows_by_name.get(ingredient_name)
        if existing is None:
            new_row = {field_name: "" for field_name in fieldnames}
            new_row.update(required_values)
            rows.append(new_row)
            rows_by_name[ingredient_name] = new_row
            continue

        for field_name, expected_value in required_values.items():
            if existing.get(field_name, "") != expected_value:
                raise RecipeDataCleaningError(
                    f"{ingredient_name}.{field_name} 应为 {expected_value!r}"
                )
    return rows


def validate_cleaned_data(
    recipes: list[dict[str, Any]],
    ingredient_rows: list[dict[str, str]],
) -> dict[str, int]:
    """校验清洗结果并返回稳定统计。"""

    recipe_names = [recipe.get("name") for recipe in recipes]
    if len(recipe_names) != len(set(recipe_names)):
        raise RecipeDataCleaningError("清洗后仍存在重复菜名")
    if any(name in REMOVED_RECIPE_NAMES for name in recipe_names):
        raise RecipeDataCleaningError("清洗后仍存在应删除的设备程序模板")

    ingredient_counts: Counter[str] = Counter()
    label_counts: Counter[str] = Counter()
    atomic_step_count = 0
    for recipe in recipes:
        recipe_name = recipe.get("name")
        ingredients = recipe.get("ingredients")
        if not isinstance(ingredients, dict) or not ingredients:
            raise RecipeDataCleaningError(f"{recipe_name} 没有有效食材")
        ingredient_counts.update(ingredients.keys())

        labels = recipe.get("labels")
        atomic_steps = recipe.get("atomic_steps")
        if not isinstance(labels, list) or not isinstance(atomic_steps, list):
            raise RecipeDataCleaningError(f"{recipe_name} 的标签或步骤类型错误")
        if not labels:
            raise RecipeDataCleaningError(f"{recipe_name} 的正式菜谱标签不能为空")
        label_counts.update(labels)
        atomic_step_count += len(atomic_steps)

        estimates = recipe.get("fuzzy_quantity_estimates", [])
        if not isinstance(estimates, list):
            raise RecipeDataCleaningError(f"{recipe_name} 的模糊量词记录类型错误")
        for estimate in estimates:
            if not isinstance(estimate, dict):
                raise RecipeDataCleaningError(f"{recipe_name} 的模糊量词记录类型错误")
            canonical_name = estimate.get("ingredient_canonical")
            if canonical_name in _forbidden_old_ingredient_names():
                raise RecipeDataCleaningError(
                    f"{recipe_name} 的模糊量词记录仍含旧食材名：{canonical_name}"
                )

    remaining_old_names = sorted(
        name
        for name in _forbidden_old_ingredient_names()
        if ingredient_counts[name] > 0
    )
    if remaining_old_names:
        raise RecipeDataCleaningError(
            f"清洗后仍存在旧食材名：{', '.join(remaining_old_names)}"
        )

    standard_rows = {
        row.get("标准食材名", ""): row
        for row in ingredient_rows
        if row.get("标准食材名", "")
    }
    missing_standard_names = sorted(set(ingredient_counts) - set(standard_rows))
    if missing_standard_names:
        raise RecipeDataCleaningError(
            f"菜谱食材未命中标准表：{', '.join(missing_standard_names)}"
        )

    missing_categories = sorted(
        name
        for name in ingredient_counts
        if not standard_rows[name].get("分类", "")
    )
    if missing_categories:
        raise RecipeDataCleaningError(
            f"被使用的标准食材缺少分类：{', '.join(missing_categories)}"
        )

    missing_auxiliary_names = sorted(
        AUXILIARY_INGREDIENT_NAMES - set(standard_rows)
    )
    if missing_auxiliary_names:
        raise RecipeDataCleaningError(
            f"非核心食材清单存在未收录标准名：{', '.join(missing_auxiliary_names)}"
        )

    lobster = next(
        (recipe for recipe in recipes if recipe.get("name") == "金汤蒜蓉小龙虾"),
        None,
    )
    if lobster is None or lobster.get("ingredients") != RESTORED_LOBSTER_INGREDIENTS:
        raise RecipeDataCleaningError("金汤蒜蓉小龙虾的食材未按原始 CSV 恢复")

    statistics = {
        "recipes": len(recipes),
        "used_ingredients": len(ingredient_counts),
        "ingredient_occurrences": sum(ingredient_counts.values()),
        "atomic_steps": atomic_step_count,
        "labels": len(label_counts),
        "label_occurrences": sum(label_counts.values()),
    }
    expected_statistics = {
        "recipes": EXPECTED_RECIPE_COUNT,
        "used_ingredients": EXPECTED_USED_INGREDIENT_COUNT,
        "ingredient_occurrences": EXPECTED_INGREDIENT_OCCURRENCE_COUNT,
        "atomic_steps": EXPECTED_ATOMIC_STEP_COUNT,
        "labels": EXPECTED_LABEL_COUNT,
        "label_occurrences": EXPECTED_LABEL_OCCURRENCE_COUNT,
    }
    if statistics != expected_statistics:
        raise RecipeDataCleaningError(
            f"清洗后统计不符合预期：实际 {statistics}，预期 {expected_statistics}"
        )
    return statistics


def _forbidden_old_ingredient_names() -> frozenset[str]:
    return frozenset(
        {
            *INGREDIENT_NAME_MAPPING,
            *REMOVED_OPERATION_MATERIALS,
            "主料",
            "自定义食材",
            "自定义",
            "食材1",
        }
    )


def write_recipes(recipes: list[dict[str, Any]]) -> None:
    RECIPE_PATH.write_text(
        json.dumps(recipes, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_ingredient_rows(
    fieldnames: list[str],
    rows: list[dict[str, str]],
) -> None:
    with INGREDIENT_PATH.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="清洗菜品食材并校验 Spec_02 所需的数据前置条件。",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验当前文件，不写入任何数据。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    recipes = load_recipes()
    fieldnames, ingredient_rows = load_ingredient_rows()

    if not args.check:
        recipes = clean_recipes(recipes)
        ingredient_rows = ensure_added_ingredient_rows(fieldnames, ingredient_rows)
        validate_cleaned_data(recipes, ingredient_rows)
        write_recipes(recipes)
        write_ingredient_rows(fieldnames, ingredient_rows)

    statistics = validate_cleaned_data(recipes, ingredient_rows)
    print(json.dumps(statistics, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
