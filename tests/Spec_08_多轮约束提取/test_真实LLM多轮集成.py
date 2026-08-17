from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.infrastructure.database import create_session_factory


REPO_ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = REPO_ROOT / "datas" / "raw" / "对话用例.json"


def load_multi_turn_cases() -> list[dict[str, Any]]:
    with CASES_PATH.open(encoding="utf-8") as stream:
        data = json.load(stream)
    return sorted(
        (case for case in data if 15 <= case["id"] <= 20),
        key=lambda case: case["id"],
    )


def _merged_tastes(merged: dict[str, Any]) -> dict[str, bool]:
    tastes: dict[str, bool] = {}
    for dish in merged["dishes"]:
        tastes.update(dish["taste_preferences"])
    return tastes


def _any_dish_contains(
    merged: dict[str, Any],
    field: str,
    expected: str,
) -> bool:
    return any(
        expected in dish[field] for dish in merged["dishes"]
    )


def _any_dish_type(merged: dict[str, Any], expected: str) -> bool:
    return any(dish["dish_type"] == expected for dish in merged["dishes"])


def _any_required_ingredient_value(
    merged: dict[str, Any],
    expected: str,
) -> bool:
    return any(
        requirement["value"] == expected
        for dish in merged["dishes"]
        for requirement in dish["required_ingredients"]
    )


def _run_case(
    service: object,
    profile_id: int,
    case: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    session_id = service.create_session(profile_id)
    results = [
        service.submit_turn(session_id, message)
        for message in case["user_messages"]
    ]
    return results, results[-1]["merged_constraints"]


@pytest.mark.integration
def test_真实LLM多轮约束提取_对话用例15到20(
    production_contract,
    db_engine,
    build_service,
    profile_id,
    seed_ingredients,
):
    extractor = (
        production_contract
        .create_langchain_multi_turn_extractor_from_environment()
    )
    service = build_service(
        create_session_factory(db_engine),
        extractor,
    )
    cases = load_multi_turn_cases()
    assert len(cases) == 6

    # 用例15:晚餐 → 不辣 + 清淡(口味改口)
    results, merged = _run_case(service, profile_id, cases[0])
    assert results[-1]["status"] == "ready_for_planning"
    assert merged["meal_periods"] == ["晚餐"]
    tastes = _merged_tastes(merged)
    assert tastes.get("is_spicy") is False
    assert tastes.get("is_light") is True

    # 用例16:午餐 → 2人 + 减脂
    results, merged = _run_case(service, profile_id, cases[1])
    assert results[-1]["status"] == "ready_for_planning"
    assert merged["meal_periods"] == ["午餐"]
    assert merged["diner_count"] == 2
    assert _any_dish_contains(merged, "effects", "减脂")

    # 用例17:早餐 → 别太甜 + 10分钟
    results, merged = _run_case(service, profile_id, cases[2])
    assert results[-1]["status"] == "ready_for_planning"
    assert merged["meal_periods"] == ["早餐"]
    assert merged["max_total_time_minutes"] == 10
    assert _merged_tastes(merged).get("is_sweet") is False

    # 用例18:正式→西餐风味；别整得太难做→难度上限中等
    results, merged = _run_case(service, profile_id, cases[3])
    assert results[-1]["status"] == "ready_for_planning"
    assert merged["meal_periods"] == []
    assert merged["diner_count"] == 6
    assert merged["max_difficulty"] == "中等"
    assert _any_dish_type(merged, "菜")
    assert _any_dish_contains(merged, "cuisines", "西餐风味")

    # 用例19:补气血→贫血；家常一点→难度上限简单
    results, merged = _run_case(service, profile_id, cases[4])
    assert results[-1]["status"] == "ready_for_planning"
    assert merged["meal_periods"] == ["晚餐"]
    assert _any_dish_contains(merged, "effects", "贫血")
    assert merged["max_difficulty"] == "简单"

    # 用例20:口味冲突拆两组；45分钟与难度上限中等并存
    results, merged = _run_case(service, profile_id, cases[5])
    assert results[-1]["status"] == "ready_for_planning"
    assert merged["meal_periods"] == ["晚餐"]
    assert merged["diner_count"] == 2
    assert merged["total_dish_count"] is None
    assert merged["max_total_time_minutes"] == 45
    assert merged["max_difficulty"] == "中等"
    assert len(merged["dishes"]) == 2
    assert [dish["count"] for dish in merged["dishes"]] == [None, None]
    assert {
        dish["taste_preferences"].get("is_spicy")
        for dish in merged["dishes"]
    } == {True, False}
    assert _any_dish_type(merged, "菜")
    assert (
        _any_required_ingredient_value(merged, "鱼")
        or _any_required_ingredient_value(merged, "鸡翅")
    )
