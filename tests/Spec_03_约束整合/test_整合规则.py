from __future__ import annotations

from spec03_support import (
    build_dialogue_constraints,
    build_dish,
    build_profile_constraints,
    invoke_integrate,
    production_contract,
)


def test_档案口味作为默认值且Dish明确口味局部优先(invoke_integrate):
    profile_constraints = build_profile_constraints(
        taste_preferences={"is_light": True, "is_spicy": True},
    )
    dialogue_constraints = build_dialogue_constraints(
        dishes=[
            build_dish(taste_preferences={"is_spicy": False}),
            build_dish(),
        ],
        evidence={
            "dishes[0].taste_preferences.is_spicy": "第一道不要辣"
        },
    )

    result = invoke_integrate(profile_constraints, dialogue_constraints)

    assert [dish["taste_preferences"] for dish in result["dishes"]] == [
        {"is_light": True, "is_spicy": False},
        {"is_light": True, "is_spicy": True},
    ]


def test_档案人群与Dish人群合并(invoke_integrate):
    profile_constraints = build_profile_constraints(
        special_populations=["孕妇", "高血压"],
    )
    dialogue_constraints = build_dialogue_constraints(
        dishes=[
            build_dish(special_populations=["儿童", "老人"]),
        ],
        evidence={
            "dishes[0].special_populations[0]": "儿童",
            "dishes[0].special_populations[1]": "老人",
        },
    )

    result = invoke_integrate(profile_constraints, dialogue_constraints)

    assert result["dishes"][0]["special_populations"] == [
        "孕妇",
        "高血压",
        "儿童",
        "老人",
    ]


def test_同名过敏与必需食材保留双方并记录冲突(invoke_integrate):
    profile_constraints = build_profile_constraints(allergens=["花生"])
    required_ingredient = {"kind": "ingredient", "value": "花生"}
    dialogue_constraints = build_dialogue_constraints(
        dishes=[
            build_dish(
                required_ingredient_groups=[
                    {"match": "all", "items": [required_ingredient]}
                ]
            ),
        ],
        evidence={
            "dishes[0].required_ingredient_groups[0].match": "想吃花生",
            "dishes[0].required_ingredient_groups[0].items[0].value": "想吃花生",
        },
    )

    result = invoke_integrate(profile_constraints, dialogue_constraints)

    assert result["allergens"] == ["花生"]
    assert result["dishes"][0]["required_ingredient_groups"] == [
        {"match": "all", "items": [required_ingredient]}
    ]
    assert result["has_conflicts"] is True
    assert result["conflicts"] == [
        {
            "code": "allergen_required_ingredient",
            "dish_index": 0,
            "profile_path": "allergens[0]",
            "dialogue_path": (
                "dishes[0].required_ingredient_groups[0].items[0].value"
            ),
            "allergen": "花生",
            "required_ingredient": required_ingredient,
            "dialogue_evidence": "想吃花生",
        }
    ]
