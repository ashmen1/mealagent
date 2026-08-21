from __future__ import annotations

import pytest

from backend.services.answer_composer import AnswerComposerService

from .conftest import build_dish_recommendation, build_generation_result


def build_composer() -> AnswerComposerService:
    return AnswerComposerService()


def test_推荐成功包含餐次人数菜名与理由() -> None:
    result = build_generation_result("recommended")
    answer = build_composer().compose(result)

    assert "晚餐" in answer
    assert "2人" in answer
    assert "番茄炒蛋" in answer
    assert "清蒸鲈鱼" in answer
    assert "清淡" in answer
    assert "高血压" in answer
    assert "12分" in answer


def test_每个菜名都在回答中出现且不增改() -> None:
    dishes = (
        build_dish_recommendation("番茄炒蛋"),
        build_dish_recommendation("清蒸鲈鱼"),
    )
    result = build_generation_result(
        "recommended",
        recommendation_reason_result={
            "profile_id": 25,
            "dialogue_id": 101,
            "dish_recommendations": list(dishes),
            "menu_reasons": [],
        },
    )
    answer = build_composer().compose(result)

    for dish in dishes:
        assert dish["recipe_name"] in answer


def test_低于八分时附质量提示() -> None:
    result = build_generation_result(
        "recommended",
        quality_warnings=[
            {
                "code": "nutrition_score_below_target",
                "nutrition_score": 6,
                "target_score": 8,
            }
        ],
    )
    answer = build_composer().compose(result)

    assert "6分" in answer
    assert "8分" in answer


def test_需要确认时输出确认文本() -> None:
    result = build_generation_result("needs_confirmation")
    answer = build_composer().compose(result)

    assert "请确认这次要安排早餐、午餐还是晚餐？" in answer
    assert "已确定" in answer


def test_尚无内容时输出简短提示() -> None:
    result = build_generation_result("in_progress")
    answer = build_composer().compose(result)

    assert answer.strip()
    assert "番茄炒蛋" not in answer


def test_约束冲突时输出状态说明() -> None:
    result = build_generation_result("constraint_conflict")
    answer = build_composer().compose(result)

    assert "冲突" in answer


def test_过敏词未识别时输出状态说明() -> None:
    result = build_generation_result("unmatched_allergen")
    answer = build_composer().compose(result)

    assert "红曲霉" in answer


def test_空候选时输出状态说明() -> None:
    result = build_generation_result("empty_candidate")
    answer = build_composer().compose(result)

    assert "没有" in answer


def test_规划无解时输出状态说明() -> None:
    result = build_generation_result("planning_infeasible")
    answer = build_composer().compose(result)

    assert "无法" in answer


@pytest.mark.parametrize(
    "status",
    [
        "recommended",
        "needs_confirmation",
        "in_progress",
        "constraint_conflict",
        "unmatched_allergen",
        "empty_candidate",
        "planning_infeasible",
    ],
)
def test_所有终态都返回非空回答(status: str) -> None:
    answer = build_composer().compose(build_generation_result(status))

    assert isinstance(answer, str)
    assert answer.strip()
