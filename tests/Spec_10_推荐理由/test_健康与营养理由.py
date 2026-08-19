from __future__ import annotations

from decimal import Decimal

import pytest

from .spec10_support import (
    NUTRIENTS,
    build_filtering_result,
    build_nutrient_grades,
    build_planning_result,
    calculate_score,
    expected_nutrient_details,
)


def test_健康理由保持输入顺序并使用固定规则模板(invoke_build):
    planning_result = build_planning_result(
        applied_health_constraints=["高血糖", "高血压"]
    )

    result = invoke_build(build_filtering_result(), planning_result)

    health_reasons = result["menu_reasons"][:-1]
    assert health_reasons == [
        {
            "reason_type": "health_constraint",
            "constraint": "高血糖",
            "rule": "macronutrient_energy_ratio",
            "sources": [
                {
                    "component": "menu_planning",
                    "paths": ["applied_health_constraints[0]"],
                }
            ],
            "text": (
                "考虑高血糖需求，本桌菜单规划已将蛋白质、脂肪和"
                "碳水化合物的供能比范围作为必须满足的条件。"
            ),
        },
        {
            "reason_type": "health_constraint",
            "constraint": "高血压",
            "rule": "sodium_upper_bound",
            "sources": [
                {
                    "component": "menu_planning",
                    "paths": ["applied_health_constraints[1]"],
                }
            ],
            "text": (
                "考虑高血压需求，本桌菜单规划已将钠摄入上限"
                "作为必须满足的条件。"
            ),
        },
    ]
    assert result["menu_reasons"][-1]["reason_type"] == "nutrition_summary"


def test_没有健康约束时整桌只返回营养摘要(invoke_build):
    result = invoke_build(
        build_filtering_result(),
        build_planning_result(applied_health_constraints=[]),
    )

    assert len(result["menu_reasons"]) == 1
    assert result["menu_reasons"][0]["reason_type"] == "nutrition_summary"


def test_未知已应用健康约束返回500(assert_reason_error):
    planning_result = build_planning_result(
        applied_health_constraints=["高尿酸"]
    )

    assert_reason_error(build_filtering_result(), planning_result, 500)


@pytest.mark.parametrize(
    "grade,expected_score,expected_text",
    [
        (
            "excellent",
            16,
            "本桌菜单按8项营养指标评分，满分16分，本桌得16分。"
            "能量、蛋白质、脂肪、碳水化合物、膳食纤维、钠、钙、铁"
            "处于优秀区间（每项2分）。",
        ),
        (
            "normal",
            8,
            "本桌菜单按8项营养指标评分，满分16分，本桌得8分。"
            "能量、蛋白质、脂肪、碳水化合物、膳食纤维、钠、钙、铁"
            "处于正常区间（每项1分）。",
        ),
        (
            "bad",
            0,
            "本桌菜单按8项营养指标评分，满分16分，本桌得0分。",
        ),
    ],
)
def test_营养摘要覆盖优秀正常和全零分分支(
    grade,
    expected_score,
    expected_text,
    invoke_build,
):
    grades = build_nutrient_grades(
        {nutrient: grade for nutrient, _, _ in NUTRIENTS}
    )
    planning_result = build_planning_result(
        nutrient_grades=grades,
        nutrition_score=expected_score,
    )

    result = invoke_build(build_filtering_result(), planning_result)

    summary = result["menu_reasons"][-1]
    assert summary["nutrition_score"] == expected_score
    assert summary["max_score"] == 16
    assert summary["text"] == expected_text
    assert summary["nutrient_details"] == expected_nutrient_details(grades)


def test_优秀和正常项目按固定营养顺序汇总且用分号连接(invoke_build):
    grades = build_nutrient_grades(
        {
            "energy_kcal": "normal",
            "protein_g": "bad",
            "fat_g": "excellent",
            "carbohydrate_g": "normal",
            "fiber_g": "excellent",
            "sodium_mg": "bad",
            "calcium_mg": "normal",
            "iron_mg": "excellent",
        }
    )
    planning_result = build_planning_result(
        nutrient_grades=grades,
        nutrition_score=calculate_score(grades),
    )

    result = invoke_build(build_filtering_result(), planning_result)

    assert result["menu_reasons"][-1]["text"] == (
        "本桌菜单按8项营养指标评分，满分16分，本桌得9分。"
        "脂肪、膳食纤维、铁处于优秀区间（每项2分）；"
        "能量、碳水化合物、钙处于正常区间（每项1分）。"
    )


def test_营养明细固定顺序单位等级来源并保留Decimal精度(invoke_build):
    grades = build_nutrient_grades()
    grades["energy_kcal"]["actual_value"] = Decimal("123.45006700")
    planning_result = build_planning_result(nutrient_grades=grades)

    result = invoke_build(build_filtering_result(), planning_result)

    details = result["menu_reasons"][-1]["nutrient_details"]
    assert details == expected_nutrient_details(grades)
    assert details[0]["menu_total_value"] == Decimal("123.45006700")
    assert [item["unit"] for item in details] == [
        "kcal",
        "g",
        "g",
        "g",
        "g",
        "mg",
        "mg",
        "mg",
    ]
    assert all(
        item["source"]
        == {
            "component": "menu_planning",
            "paths": [f"nutrient_grades.{item['nutrient']}"],
        }
        for item in details
    )
    assert not any(item["nutrient"] == "cholesterol_mg" for item in details)


def test_营养总分与八项分数之和不一致返回500(assert_reason_error):
    planning_result = build_planning_result()
    planning_result["nutrition_score"] += 1

    assert_reason_error(build_filtering_result(), planning_result, 500)


def test_未应用健康标签和人均营养不进入推荐理由(invoke_build):
    planning_result = build_planning_result(
        unapplied_health_constraints=["备孕", "高尿酸"],
        per_person_nutrition={"energy_kcal": Decimal("1")},
    )

    result = invoke_build(build_filtering_result(), planning_result)

    rendered = repr(result)
    assert "备孕" not in rendered
    assert "高尿酸" not in rendered
    assert "per_person_nutrition" not in rendered
