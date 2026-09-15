from __future__ import annotations

import copy
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest


TEST_DIR = Path(__file__).resolve().parent
REPO_ROOT = TEST_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from backend.services.menu_planning import MenuPlanningService
from backend.services.recommendation_reason import RecommendationReasonService
from tests.Spec_06_菜单规划.spec06_support import (
    build_candidate,
    build_dish,
    build_nutrition,
    build_planning_input,
)
from tests.Spec_10_推荐理由.conftest import _build_decision_context
from tests.Spec_10_推荐理由.spec10_support import (
    build_candidate as build_reason_candidate,
    build_filtering_result,
    build_planning_result,
    build_selected_dish,
)
from tests.Spec_14_对外服务接口.conftest import (
    FakeRecommendationService,
    build_generation_result,
)
from tests.Spec_14_对外服务接口.test_API层 import build_app, parse_sse


PAIRING_FIELDS = {
    "composition_type",
    "serving_temperature",
    "primary_cooking_method",
}


def _pairing_candidate(
    recipe_name: str,
    composition_type: str,
    serving_temperature: str,
    primary_cooking_method: str,
    *,
    recipe_type: str = "菜",
    matched_tags: list[str] | None = None,
    nutrition: dict[str, Decimal] | None = None,
) -> dict[str, Any]:
    candidate = build_candidate(
        recipe_name,
        recipe_type=recipe_type,
        matched_tags=matched_tags,
        nutrition=nutrition,
    )
    candidate.update(
        {
            "composition_type": composition_type,
            "serving_temperature": serving_temperature,
            "primary_cooking_method": primary_cooking_method,
        }
    )
    return candidate


def _plan_candidates(
    candidates: list[dict[str, Any]],
    count: int,
    *,
    menu_change_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    planning_input = build_planning_input(
        total_dish_count=count,
        dishes=[build_dish(count=count, candidates=candidates)],
    )
    if menu_change_policy is not None:
        planning_input["menu_change_policy"] = copy.deepcopy(
            menu_change_policy
        )
    return MenuPlanningService().plan(planning_input)


def _selected_names(result: dict[str, Any]) -> list[str]:
    return [item["recipe_name"] for item in result["selected_dishes"]]


def _selected_pairing_values(
    result: dict[str, Any],
    field: str,
) -> list[str]:
    return [item[field] for item in result["selected_dishes"]]


def test_plan正常路径保留三项菜谱属性() -> None:
    candidate = _pairing_candidate("清蒸鸡", "荤", "热", "蒸")

    result = _plan_candidates([candidate], 1)

    assert _selected_names(result) == ["清蒸鸡"]
    selected = result["selected_dishes"][0]
    assert {field: selected[field] for field in PAIRING_FIELDS} == {
        "composition_type": "荤",
        "serving_temperature": "热",
        "primary_cooking_method": "蒸",
    }


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("composition_type", None),
        ("composition_type", "蛋奶素"),
        ("serving_temperature", None),
        ("serving_temperature", "常温"),
        ("primary_cooking_method", None),
        ("primary_cooking_method", "微波"),
    ],
)
def test_规划候选属性缺失或非法返回400且不使用默认值(
    field: str,
    invalid_value: str | None,
) -> None:
    candidate = _pairing_candidate("测试菜", "荤", "热", "炒")
    if invalid_value is None:
        candidate.pop(field)
    else:
        candidate[field] = invalid_value
    planning_input = build_planning_input(
        dishes=[build_dish(candidates=[candidate])]
    )

    with pytest.raises(Exception) as captured:
        MenuPlanningService().plan(planning_input)

    assert getattr(captured.value, "status_code", None) == 400
    assert field in str(captured.value), (
        f"输入={candidate!r}；期望错误指出字段={field}；"
        f"实际错误={captured.value}"
    )


def test_全部菜品类型都计入荤素和冷热统计() -> None:
    candidates = [
        _pairing_candidate("菜类荤菜", "荤", "热", "蒸", recipe_type="菜"),
        _pairing_candidate("肉汤", "荤", "热", "蒸", recipe_type="汤"),
        _pairing_candidate("主食荤菜", "荤", "热", "蒸", recipe_type="主食"),
        _pairing_candidate("素小菜", "素", "冷", "蒸", recipe_type="小菜"),
        _pairing_candidate("甜品素菜", "素", "冷", "蒸", recipe_type="甜品"),
        _pairing_candidate("候补素菜", "素", "热", "蒸", recipe_type="菜"),
    ]

    result = _plan_candidates(candidates, 5)
    names = _selected_names(result)

    assert "候补素菜" in names
    assert "甜品素菜" in names
    assert "主食荤菜" not in names
    assert _selected_pairing_values(result, "composition_type").count("素") == 3


def test_一项菜单不评价荤素而三项菜单优先一荤两素() -> None:
    one_dish = _plan_candidates(
        [
            _pairing_candidate("顺序第一的荤菜", "荤", "热", "蒸"),
            _pairing_candidate("顺序第二的素菜", "素", "热", "蒸"),
        ],
        1,
    )
    three_dishes = _plan_candidates(
        [
            _pairing_candidate("荤菜1", "荤", "热", "蒸"),
            _pairing_candidate("荤菜2", "荤", "热", "蒸"),
            _pairing_candidate("荤菜3", "荤", "热", "蒸"),
            _pairing_candidate("素菜1", "素", "热", "蒸"),
            _pairing_candidate("素菜2", "素", "热", "蒸"),
        ],
        3,
    )

    assert _selected_names(one_dish) == ["顺序第一的荤菜"]
    compositions = _selected_pairing_values(
        three_dishes,
        "composition_type",
    )
    assert compositions.count("荤") == 1
    assert compositions.count("素") == 2


def test_三项菜单不评价冷热而四项菜单优先冷热兼有且热多于冷() -> None:
    three_dishes = _plan_candidates(
        [
            _pairing_candidate("热菜1", "荤", "热", "蒸"),
            _pairing_candidate("热菜2", "荤", "热", "蒸"),
            _pairing_candidate("热菜3", "荤", "热", "蒸"),
            _pairing_candidate("候补冷菜", "荤", "冷", "蒸"),
        ],
        3,
    )
    four_dishes = _plan_candidates(
        [
            _pairing_candidate("四项热菜1", "荤", "热", "蒸"),
            _pairing_candidate("四项热菜2", "荤", "热", "蒸"),
            _pairing_candidate("四项热菜3", "荤", "热", "蒸"),
            _pairing_candidate("四项热菜4", "荤", "热", "蒸"),
            _pairing_candidate("四项候补冷菜", "荤", "冷", "蒸"),
        ],
        4,
    )

    assert _selected_pairing_values(
        three_dishes,
        "serving_temperature",
    ) == ["热", "热", "热"]
    temperatures = _selected_pairing_values(
        four_dishes,
        "serving_temperature",
    )
    assert temperatures.count("冷") >= 1
    assert temperatures.count("热") > temperatures.count("冷")


def test_荤素冷热相同时优先不同主做法更多的菜单() -> None:
    candidates = [
        _pairing_candidate("蒸荤菜", "荤", "热", "蒸"),
        _pairing_candidate("蒸素菜1", "素", "热", "蒸"),
        _pairing_candidate("蒸素菜2", "素", "热", "蒸"),
        _pairing_candidate("煮素菜", "素", "热", "煮"),
        _pairing_candidate("炒素菜", "素", "热", "炒"),
    ]

    result = _plan_candidates(candidates, 3)

    assert set(
        _selected_pairing_values(result, "primary_cooking_method")
    ) == {"蒸", "煮", "炒"}


def test_完整选优顺序为营养异常项荤素冷热做法标签和原顺序() -> None:
    zero_nutrition = {
        field: Decimal("0") for field in build_nutrition()
    }
    fixed_meat = _pairing_candidate(
        "固定荤菜",
        "荤",
        "热",
        "蒸",
        nutrition=zero_nutrition,
    )

    def choose(variable_candidates: list[dict[str, Any]]) -> str:
        planning_input = build_planning_input(
            total_dish_count=2,
            dishes=[
                build_dish(count=1, candidates=variable_candidates),
                build_dish(count=1, candidates=[fixed_meat]),
            ],
        )
        selected = MenuPlanningService().plan(planning_input)[
            "selected_dishes"
        ]
        return next(
            item["recipe_name"]
            for item in selected
            if item["recipe_name"] != "固定荤菜"
        )

    assert choose(
        [
            _pairing_candidate(
                "搭配更好但营养差",
                "素",
                "热",
                "蒸",
                nutrition=zero_nutrition,
            ),
            _pairing_candidate(
                "营养得分更高",
                "荤",
                "热",
                "蒸",
                nutrition=build_nutrition(),
            ),
        ]
    ) == "营养得分更高"
    assert choose(
        [
            _pairing_candidate(
                "一个异常项但搭配好",
                "素",
                "热",
                "蒸",
                nutrition=build_nutrition(energy_kcal="630"),
            ),
            _pairing_candidate(
                "异常项更少",
                "荤",
                "热",
                "蒸",
                nutrition=build_nutrition(energy_kcal="680", fiber_g="9"),
            ),
        ]
    ) == "异常项更少"
    assert choose(
        [
            _pairing_candidate(
                "标签多但荤素差",
                "荤",
                "热",
                "蒸",
                matched_tags=["晚餐", "清淡", "粤菜"],
                nutrition=build_nutrition(),
            ),
            _pairing_candidate(
                "荤素更好",
                "素",
                "热",
                "蒸",
                matched_tags=[],
                nutrition=build_nutrition(),
            ),
        ]
    ) == "荤素更好"
    assert choose(
        [
            _pairing_candidate(
                "顺序靠前但标签少",
                "素",
                "热",
                "蒸",
                matched_tags=[],
                nutrition=build_nutrition(),
            ),
            _pairing_candidate(
                "标签更多",
                "素",
                "热",
                "蒸",
                matched_tags=["晚餐"],
                nutrition=build_nutrition(),
            ),
        ]
    ) == "标签更多"
    assert choose(
        [
            _pairing_candidate(
                "完全同分第一项",
                "素",
                "热",
                "蒸",
                nutrition=build_nutrition(),
            ),
            _pairing_candidate(
                "完全同分第二项",
                "素",
                "热",
                "蒸",
                nutrition=build_nutrition(),
            ),
        ]
    ) == "完全同分第一项"


def test_软目标不制造无解且不得覆盖换菜保留约束() -> None:
    candidates = [
        _pairing_candidate("历史荤菜1", "荤", "热", "蒸"),
        _pairing_candidate("历史荤菜2", "荤", "热", "蒸"),
        _pairing_candidate("候补素菜", "素", "冷", "炒"),
    ]
    all_meat = _plan_candidates(candidates[:2], 2)
    retained = _plan_candidates(
        candidates,
        2,
        menu_change_policy={
            "previous_recipe_names": ["历史荤菜1", "历史荤菜2"],
            "required_previous_count": 2,
            "required_recipe_names": [],
            "forbidden_recipe_names": [],
        },
    )

    assert set(_selected_names(all_meat)) == {"历史荤菜1", "历史荤菜2"}
    assert set(_selected_names(retained)) == {"历史荤菜1", "历史荤菜2"}


def _build_pairing_reason_result() -> tuple[dict[str, Any], dict[str, Any]]:
    attributes = [
        ("炒鸡片", "荤", "热", "炒"),
        ("清蒸鱼", "荤", "热", "蒸"),
        ("青菜汤", "素", "热", "煮"),
        ("凉拌豆腐", "素", "冷", "拌"),
    ]
    candidates = [
        build_reason_candidate(
            name,
            composition_type=composition,
            serving_temperature=temperature,
            primary_cooking_method=method,
        )
        for name, composition, temperature, method in attributes
    ]
    selected = [
        build_selected_dish(
            0,
            name,
            composition_type=composition,
            serving_temperature=temperature,
            primary_cooking_method=method,
        )
        for name, composition, temperature, method in attributes
    ]
    filtering = build_filtering_result(dishes=[candidates])
    planning = build_planning_result(
        selected_dishes=selected,
        diner_count=4,
    )
    context = _build_decision_context(filtering, planning)
    context["effective_constraints"]["total_dish_count"] = 4
    context["effective_constraints"]["dishes"][0]["count"] = 4
    reasons = RecommendationReasonService().build(
        filtering,
        planning,
        context,
    )
    return reasons, planning


def test_推荐理由说明实际荤素冷热和主做法且不虚构达标() -> None:
    reasons, _ = _build_pairing_reason_result()

    pairing_reason = next(
        reason
        for reason in reasons["planning_reasons"]
        if reason["rule"] == "menu_pairing"
    )
    text = pairing_reason["text"]

    assert "荤2" in text and "素2" in text
    assert "热3" in text and "冷1" in text
    assert all(method in text for method in ("炒", "蒸", "煮", "拌"))
    assert "已达标" not in text


def test_HTTP正常路径保持字段不变并在回答中展示搭配说明() -> None:
    reasons, planning = _build_pairing_reason_result()
    recommendation = FakeRecommendationService(
        results=[
            build_generation_result(
                recommendation_reason_result=reasons,
                menu_planning_result=planning,
            )
        ]
    )
    with build_app(recommendation=recommendation) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "profile_id": 25,
                "messages": [{"role": "user", "content": "安排四道晚餐"}],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"id", "object", "choices", "session_id", "status"}
    answer = body["choices"][0]["message"]["content"]
    assert "荤2" in answer and "素2" in answer
    assert "热3" in answer and "冷1" in answer
    assert all(method in answer for method in ("炒", "蒸", "煮", "拌"))


def test_SSE正常路径保持协议不变并流式输出搭配说明() -> None:
    reasons, planning = _build_pairing_reason_result()
    recommendation = FakeRecommendationService(
        results=[
            build_generation_result(
                recommendation_reason_result=reasons,
                menu_planning_result=planning,
            )
        ]
    )
    with build_app(recommendation=recommendation) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "profile_id": 25,
                "messages": [{"role": "user", "content": "安排四道晚餐"}],
                "stream": True,
            },
            headers={"Accept": "text/event-stream"},
        )

    chunks = parse_sse(response)
    answer = "".join(
        chunk["choices"][0]["delta"].get("content", "")
        for chunk in chunks
    )

    assert response.status_code == 200
    assert response.headers["x-session-id"] == "101"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert "荤2" in answer and "素2" in answer
    assert "热3" in answer and "冷1" in answer
