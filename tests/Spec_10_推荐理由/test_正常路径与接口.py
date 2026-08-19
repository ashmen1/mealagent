from __future__ import annotations

from copy import deepcopy

import pytest

from .spec10_support import (
    build_candidate,
    build_filtering_result,
    build_planning_result,
    expected_nutrient_details,
)


def test_正常路径返回逐菜与整桌推荐理由(invoke_build):
    filtering_result = build_filtering_result(
        dishes=[
            [
                build_candidate("未选中的菜", [], []),
                build_candidate(
                    "白灼芥蓝",
                    ["晚餐", "清淡", "粤菜"],
                    ["餐次", "口味", "菜系"],
                ),
            ]
        ],
        ignored_filtering_field="不应输出",
    )
    planning_result = build_planning_result(
        applied_health_constraints=["高血压", "高血糖"],
        ignored_planning_field="不应输出",
    )
    grades = planning_result["nutrient_grades"]

    result = invoke_build(filtering_result, planning_result)

    assert result == {
        "profile_id": 25,
        "dialogue_id": 101,
        "dish_recommendations": [
            {
                "dish_constraint_index": 0,
                "recipe_name": "白灼芥蓝",
                "reasons": [
                    {
                        "reason_type": "tag_match",
                        "matched_group": "餐次",
                        "matched_tags": ["晚餐"],
                        "sources": [
                            {
                                "component": "menu_planning",
                                "paths": [
                                    "selected_dishes[0].dish_constraint_index",
                                    "selected_dishes[0].recipe_name",
                                ],
                            },
                            {
                                "component": "dish_filtering",
                                "paths": [
                                    "dishes[0][1].matched_tags",
                                    "dishes[0][1].matched_groups",
                                ],
                            },
                        ],
                        "text": "白灼芥蓝适合本次晚餐。",
                    },
                    {
                        "reason_type": "tag_match",
                        "matched_group": "口味",
                        "matched_tags": ["清淡"],
                        "sources": [
                            {
                                "component": "menu_planning",
                                "paths": [
                                    "selected_dishes[0].dish_constraint_index",
                                    "selected_dishes[0].recipe_name",
                                ],
                            },
                            {
                                "component": "dish_filtering",
                                "paths": [
                                    "dishes[0][1].matched_tags",
                                    "dishes[0][1].matched_groups",
                                ],
                            },
                        ],
                        "text": "白灼芥蓝符合本次清淡口味偏好。",
                    },
                    {
                        "reason_type": "tag_match",
                        "matched_group": "菜系",
                        "matched_tags": ["粤菜"],
                        "sources": [
                            {
                                "component": "menu_planning",
                                "paths": [
                                    "selected_dishes[0].dish_constraint_index",
                                    "selected_dishes[0].recipe_name",
                                ],
                            },
                            {
                                "component": "dish_filtering",
                                "paths": [
                                    "dishes[0][1].matched_tags",
                                    "dishes[0][1].matched_groups",
                                ],
                            },
                        ],
                        "text": "白灼芥蓝符合本次粤菜偏好。",
                    },
                ],
            }
        ],
        "menu_reasons": [
            {
                "reason_type": "health_constraint",
                "constraint": "高血压",
                "rule": "sodium_upper_bound",
                "sources": [
                    {
                        "component": "menu_planning",
                        "paths": ["applied_health_constraints[0]"],
                    }
                ],
                "text": (
                    "考虑高血压需求，本桌菜单规划已将钠摄入上限"
                    "作为必须满足的条件。"
                ),
            },
            {
                "reason_type": "health_constraint",
                "constraint": "高血糖",
                "rule": "macronutrient_energy_ratio",
                "sources": [
                    {
                        "component": "menu_planning",
                        "paths": ["applied_health_constraints[1]"],
                    }
                ],
                "text": (
                    "考虑高血糖需求，本桌菜单规划已将蛋白质、脂肪和"
                    "碳水化合物的供能比范围作为必须满足的条件。"
                ),
            },
            {
                "reason_type": "nutrition_summary",
                "nutrition_score": 9,
                "max_score": 16,
                "nutrient_details": expected_nutrient_details(grades),
                "sources": [
                    {
                        "component": "menu_planning",
                        "paths": ["nutrition_score"],
                    }
                ],
                "text": (
                    "本桌菜单按8项营养指标评分，满分16分，本桌得9分。"
                    "能量、蛋白质、钠处于优秀区间（每项2分）；"
                    "脂肪、碳水化合物、钙处于正常区间（每项1分）。"
                ),
            },
        ],
    }


def test_构造函数不接受依赖参数(production_contract):
    with pytest.raises(TypeError):
        production_contract.RecommendationReasonService(object())


def test_调用后不修改输入且相同输入结果确定(invoke_build):
    filtering_result = build_filtering_result()
    planning_result = build_planning_result()
    filtering_before = deepcopy(filtering_result)
    planning_before = deepcopy(planning_result)

    first = invoke_build(filtering_result, planning_result)
    second = invoke_build(filtering_result, planning_result)

    assert filtering_result == filtering_before
    assert planning_result == planning_before
    assert first == second
