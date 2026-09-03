from __future__ import annotations

from decimal import Decimal

from backend.scripts.audit_50x20_acceptance import (
    NUTRIENT_FIELDS,
    _audit_final_cases,
)
from backend.services.acceptance_audit import RecipeAuditRecord, ReportCase


def _recipe() -> RecipeAuditRecord:
    return RecipeAuditRecord(
        name="番茄鸡蛋",
        is_recommendable=True,
        tags=frozenset({"晚餐"}),
        difficulty="简单",
        total_time_minutes=10,
        dish_type="菜",
        ingredients=frozenset({"番茄", "鸡蛋"}),
        core_ingredients=frozenset({"番茄", "鸡蛋"}),
        ingredient_categories={"番茄": "蔬菜", "鸡蛋": "蛋类"},
        nutrition={field: Decimal("1.00") for field in NUTRIENT_FIELDS},
    )


def _report_case(*names: str) -> ReportCase:
    return ReportCase(
        dialogue_id=1,
        turn_number=1,
        user_message="安排晚饭",
        profile_id=1,
        hard_text="餐次：晚餐",
        soft_text="",
        generation_status="recommended",
        answer_text="",
        selected_recipes=tuple(names),
        answer_diner_count=1,
    )


def _frozen_case(candidate_name: str, nutrition_value: str) -> dict[str, object]:
    nutrition = {field: nutrition_value for field in NUTRIENT_FIELDS}
    return {
        "dialogue_id": 1,
        "profile_id": 1,
        "status": "recommended",
        "selected_recipes": ["番茄鸡蛋"],
        "generation_result": {
            "menu_planning_result": {
                "selected_dishes": [
                    {
                        "dish_constraint_index": 0,
                        "recipe_name": "番茄鸡蛋",
                        "nutrition": nutrition,
                    }
                ]
            },
            "dish_filtering_audit": {
                "selected_candidates": [
                    {
                        "dish_constraint_index": 0,
                        "candidate": {"recipe_name": candidate_name},
                    }
                ]
            },
        },
    }


def test_候选来源与营养反例会被追溯规则发现() -> None:
    recipe = _recipe()
    catalog = {recipe.name: recipe}

    result = _audit_final_cases(
        [_frozen_case("另一道菜", "2.00")],
        [_report_case(recipe.name)],
        catalog,
        catalog,
        catalog,
    )

    assert result["violation_count"] == 2
    assert {item["rule"] for item in result["violations"]} == {
        "candidate_traceability",
        "selected_nutrition_matches_postgresql",
    }


def test_跨执行产物菜单不同只作提示不误判真实性() -> None:
    recipe = _recipe()
    catalog = {recipe.name: recipe}

    result = _audit_final_cases(
        [_frozen_case(recipe.name, "1.00")],
        [_report_case("另一道菜")],
        catalog,
        catalog,
        catalog,
    )

    assert result["violation_count"] == 0
    assert result["cross_artifact_difference_count"] == 1
    assert result["cross_artifact_differences"][0]["rule"] == (
        "answer_selected_names_consistent"
    )
