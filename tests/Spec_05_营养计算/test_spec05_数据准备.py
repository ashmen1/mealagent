from __future__ import annotations

import json
from decimal import Decimal

from spec05_support import REPO_ROOT


RECIPE_PATH = REPO_ROOT / "datas" / "processed" / "Recipes" / "RecipeComplete.json"


def load_recipes():
    return json.loads(RECIPE_PATH.read_text(encoding="utf-8"))


def test_全部菜谱食材均内嵌最终克重与证据():
    recipes = load_recipes()
    associations = []

    for recipe in recipes:
        resolutions = recipe["ingredient_quantity_resolutions"]
        assert set(resolutions) == set(recipe["ingredients"])
        for ingredient_name, quantity_text in recipe["ingredients"].items():
            item = resolutions[ingredient_name]
            assert item["original_quantity"] == quantity_text
            assert item["calculation_path"]
            assert item["reference_source"]
            assert item["ingredient_weight_distribution"]
            associations.append(item)

    assert len(recipes) == 1913
    assert len(associations) == 16263
    assert sum(not item["is_quantity_estimated"] and not item["is_nutrition_excluded"] for item in associations) == 11342
    assert sum(item["is_quantity_estimated"] for item in associations) == 4156
    assert sum(item["is_nutrition_excluded"] for item in associations) == 765
    assert all(
        Decimal(str(item["resolved_quantity_g"])) > 0
        for item in associations
        if not item["is_nutrition_excluded"]
    )


def test_全部非水食材记录包含可校验克重分布():
    recipes = load_recipes()
    for recipe in recipes:
        for item in recipe["ingredient_quantity_resolutions"].values():
            distribution = item["ingredient_weight_distribution"]
            if item["is_nutrition_excluded"]:
                assert distribution["sample_count"] == 0
                assert distribution["method"] == "nutrition_excluded"
                continue
            values = [
                Decimal(str(distribution[field]))
                for field in ("min_g", "p25_g", "median_g", "p75_g", "max_g")
            ]
            assert distribution["sample_count"] > 0
            assert values == sorted(values)
            assert Decimal(str(distribution["mean_g"])) > 0
            assert distribution["common_values"]


def test_模糊量统计仍保留原始样本数方法和取值路径():
    recipes = load_recipes()
    fuzzy_items = [
        item
        for recipe in recipes
        for item in recipe.get("fuzzy_quantity_estimates", [])
    ]
    internal = [
        item
        for item in fuzzy_items
        if item.get("source_status") == "approved_internal_statistics"
    ]

    assert len(internal) == 392
    for item in internal:
        expected_method = (
            "nearest_rank_quantiles"
            if item["sample_count"] >= 20
            else "strict_mass_mode"
        )
        assert item["estimation_method"] == expected_method
        assert item["lower_bound_g"] <= item["point_estimate_g"] <= item["upper_bound_g"]
