from __future__ import annotations

from decimal import Decimal

import pytest

from backend.services.acceptance_audit import (
    RecipeAuditRecord,
    ReportCase,
    audit_report_case,
    compare_catalogs,
)


def _recipe(
    name: str = "合规菜",
    *,
    tags: tuple[str, ...] = ("晚餐", "清淡"),
    difficulty: str = "简单",
    total_time: int = 15,
    dish_type: str = "菜",
    ingredients: tuple[str, ...] = ("番茄", "鸡蛋"),
    core_ingredients: tuple[str, ...] | None = None,
    is_recommendable: bool = True,
) -> RecipeAuditRecord:
    cores = ingredients if core_ingredients is None else core_ingredients
    return RecipeAuditRecord(
        name=name,
        is_recommendable=is_recommendable,
        tags=frozenset(tags),
        difficulty=difficulty,
        total_time_minutes=total_time,
        dish_type=dish_type,
        ingredients=frozenset(ingredients),
        core_ingredients=frozenset(cores),
        ingredient_categories={item: "测试分类" for item in ingredients},
        nutrition={"energy_kcal": Decimal("100.00")},
    )


def _case(*recipe_names: str) -> ReportCase:
    menu = "\n".join(
        f"{index}. {name}" for index, name in enumerate(recipe_names, start=1)
    )
    return ReportCase(
        dialogue_id=1,
        turn_number=1,
        user_message="测试请求",
        profile_id=1,
        hard_text="",
        soft_text="",
        generation_status="recommended",
        answer_text=f"为您生成晚餐，1人份菜单：\n{menu}",
        selected_recipes=tuple(recipe_names),
        answer_diner_count=1,
    )


def _audit(
    recipe: RecipeAuditRecord,
    expected: dict[str, object],
    *,
    profile: dict[str, object] | None = None,
    inconsistent: frozenset[str] = frozenset(),
) -> dict[str, object]:
    catalog = {recipe.name: recipe}
    return audit_report_case(
        _case(recipe.name),
        expected,
        profile or {"allergens": [], "taste_preference": "无"},
        catalog,
        catalog,
        catalog,
        {"海鲜": frozenset({"虾"}), "面": frozenset({"面条"})},
        inconsistent,
    )


@pytest.mark.parametrize(
    ("recipe", "expected", "profile", "rule_id"),
    [
        (
            _recipe(ingredients=("虾", "番茄")),
            {},
            {"allergens": ["海鲜"], "taste_preference": "无"},
            "hard.allergen",
        ),
        (
            _recipe(tags=("晚餐", "辣")),
            {"negative_tastes": ["辣"]},
            None,
            "hard.negative_taste",
        ),
        (_recipe(tags=("午餐",)), {"meal_period": "晚餐"}, None, "hard.meal_period"),
        (_recipe(total_time=31), {"max_total_time_minutes": 30}, None, "hard.max_time"),
        (_recipe(difficulty="中等"), {"max_difficulty": "简单"}, None, "hard.max_difficulty"),
        (
            _recipe(ingredients=("番茄", "鸡蛋", "土豆")),
            {"available_ingredients": ["番茄", "鸡蛋"]},
            None,
            "hard.available_ingredients",
        ),
        (
            _recipe(dish_type="菜", ingredients=("番茄",)),
            {
                "dish_groups": [
                    {
                        "count": 1,
                        "dish_type": "主食",
                        "required_ingredient_groups": [
                            {
                                "match": "any",
                                "items": [{"kind": "concept", "value": "面"}],
                            }
                        ],
                    }
                ]
            },
            None,
            "hard.dish_groups",
        ),
    ],
)
def test_反例会被对应硬约束规则识别(
    recipe: RecipeAuditRecord,
    expected: dict[str, object],
    profile: dict[str, object] | None,
    rule_id: str,
) -> None:
    result = _audit(recipe, expected, profile=profile)

    assert result["hard_constraint_status"] == "fail"
    assert any(
        rule["rule_id"] == rule_id and rule["status"] == "fail"
        for rule in result["rules"]
    )


def test_三方任一来源缺少菜名会判真实性失败() -> None:
    recipe = _recipe()

    result = audit_report_case(
        _case(recipe.name),
        {},
        {"allergens": [], "taste_preference": "无"},
        {recipe.name: recipe},
        {recipe.name: recipe},
        {},
        {},
        frozenset(),
    )

    assert result["authenticity_status"] == "fail"
    assert any(
        rule["rule_id"] == "auth.neo4j_exists" and rule["status"] == "fail"
        for rule in result["rules"]
    )


def test_正式菜谱数据缺失时硬约束不能冒充通过() -> None:
    recipe = _recipe()

    result = audit_report_case(
        _case(recipe.name),
        {"meal_period": "晚餐"},
        {"allergens": [], "taste_preference": "无"},
        {},
        {recipe.name: recipe},
        {recipe.name: recipe},
        {},
        frozenset(),
    )

    assert result["authenticity_status"] == "fail"
    assert result["hard_constraint_status"] == "not_auditable"


def test_数据不一致不能被判为真实性通过() -> None:
    recipe = _recipe()

    result = _audit(recipe, {}, inconsistent=frozenset({recipe.name}))

    assert result["authenticity_status"] == "fail"


def test_未覆盖原始要求单列而不冒充硬约束通过() -> None:
    result = _audit(_recipe(), {"unsupported": ["食材尽量共用"]})

    assert result["hard_constraint_status"] == "pass"
    assert result["requirements_coverage_status"] == "not_auditable"
    assert any(
        rule["rule_id"] == "coverage.unsupported_requirement"
        and rule["status"] == "not_auditable"
        for rule in result["rules"]
    )


def test_重复菜名会被独立规则识别() -> None:
    recipe = _recipe()
    catalog = {recipe.name: recipe}

    result = audit_report_case(
        _case(recipe.name, recipe.name),
        {},
        {"allergens": [], "taste_preference": "无"},
        catalog,
        catalog,
        catalog,
        {},
        frozenset(),
    )

    assert result["hard_constraint_status"] == "fail"
    assert any(
        rule["rule_id"] == "hard.unique_recipe" and rule["status"] == "fail"
        for rule in result["rules"]
    )


def test_三方菜谱差异可追溯到来源和字段() -> None:
    reference = _recipe()
    graph = _recipe(tags=("晚餐",), total_time=20)

    issues = compare_catalogs(
        {reference.name: reference},
        {reference.name: reference},
        {graph.name: graph},
        graph_tag_names=frozenset({"晚餐", "清淡"}),
    )

    assert {issue["field"] for issue in issues} == {"graph_tags", "total_time_minutes"}
    assert all(issue["source"] == "Neo4j" for issue in issues)
