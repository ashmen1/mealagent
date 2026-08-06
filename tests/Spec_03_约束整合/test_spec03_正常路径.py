from __future__ import annotations

from spec03_support import (
    build_dialogue_constraints,
    build_dish,
    build_profile_constraints,
    invoke_integrate,
    production_contract,
)


def test_整合完整档案与对话约束(invoke_integrate):
    profile_constraints = build_profile_constraints(
        profile_id=25,
        special_populations=["孕妇"],
        taste_preferences={"is_light": True},
        allergens=["花生"],
    )
    dialogue_constraints = build_dialogue_constraints(
        dialogue_id=8,
        meal_periods=["晚餐"],
        diner_count=2,
        max_total_time_minutes=45,
        available_ingredients=["番茄", "鸡蛋"],
        dishes=[
            build_dish(
                count=2,
                dish_type="菜",
                taste_preferences={"is_spicy": False},
                cuisines=["川湘菜"],
                effects=["养胃健胃消食"],
                special_populations=["儿童"],
                required_ingredients=[
                    {"kind": "ingredient", "value": "鸡蛋"}
                ],
            )
        ],
        evidence={
            "meal_periods[0]": "晚餐",
            "diner_count": "两个人",
            "max_total_time_minutes": "45分钟",
            "available_ingredients[0]": "番茄",
            "available_ingredients[1]": "鸡蛋",
            "dishes[0].count": "两道菜",
            "dishes[0].dish_type": "菜",
            "dishes[0].taste_preferences.is_spicy": "不辣",
            "dishes[0].cuisines[0]": "川湘菜",
            "dishes[0].effects[0]": "养胃",
            "dishes[0].special_populations[0]": "儿童",
            "dishes[0].required_ingredients[0].value": "鸡蛋",
        },
    )

    result = invoke_integrate(profile_constraints, dialogue_constraints)

    assert result == {
        "profile_id": 25,
        "dialogue_id": 8,
        "meal_periods": ["晚餐"],
        "diner_count": 2,
        "max_total_time_minutes": 45,
        "available_ingredients": ["番茄", "鸡蛋"],
        "allergens": ["花生"],
        "dishes": [
            {
                "count": 2,
                "dish_type": "菜",
                "taste_preferences": {
                    "is_light": True,
                    "is_spicy": False,
                },
                "cuisines": ["川湘菜"],
                "effects": ["养胃健胃消食"],
                "special_populations": ["孕妇", "儿童"],
                "required_ingredients": [
                    {"kind": "ingredient", "value": "鸡蛋"}
                ],
            }
        ],
        "has_conflicts": False,
        "conflicts": [],
    }
