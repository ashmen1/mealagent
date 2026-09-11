from __future__ import annotations

import json
from typing import Any

from backend.services.answer_composer import AnswerComposerService

from .conftest import (
    FakeConfirmationService,
    FakeRecommendationService,
    build_confirmation_state,
    build_generation_result,
)
from .test_API层 import build_app, parse_sse


FILTERING_TEXTS = (
    "南瓜发糕符合本次早餐条件。",
    "已按档案中的海鲜过敏信息，在候选筛选阶段排除含相关标准食材的菜谱。",
    "本次只从允许推荐的菜谱中选择。",
    "符合条件的候选先按标签命中数从多到少排列，命中数相同时按菜名稳定排序。",
)

PLANNING_TEXTS = (
    "1人且未指定菜品总数，本次按默认数量规则选择1道菜。",
    "同名菜不重复；菜谱配方和整份营养按库中固定值计算，本次不调整食材克重。",
    "本次在优先候选范围内找到达到营养目标的可行菜单。",
    "在满足约束的菜单中，依次按营养得分高、正常区间外营养项少、标签命中多、候选顺序靠前进行选择。",
    "本次返回的是在上述规则下已证明最优的菜单。",
)

NUTRITION_TEXT = "本桌菜单按8项营养指标评分，满分16分，本桌得13分。"


def build_rule(
    reason_type: str,
    rule: str,
    text: str,
    *,
    component: str,
) -> dict[str, Any]:
    return {
        "reason_type": reason_type,
        "rule": rule,
        "details": {"applied": True},
        "affected_recipe_names": ["南瓜发糕"],
        "dish_constraint_indexes": [0],
        "sources": [{"component": component, "paths": [rule]}],
        "text": text,
    }


def build_profile_25_result(
    *,
    filtering_texts: tuple[str, ...] = FILTERING_TEXTS,
    planning_texts: tuple[str, ...] = PLANNING_TEXTS,
    menu_reasons: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    reason_result = {
        "profile_id": 25,
        "dialogue_id": 101,
        "dish_recommendations": [
            {
                "dish_constraint_index": 0,
                "recipe_name": "南瓜发糕",
                "reasons": [],
            }
        ],
        "filtering_reasons": [
            build_rule(
                "filtering_rule",
                f"filtering_{index}",
                text,
                component="dish_filtering",
            )
            for index, text in enumerate(filtering_texts)
        ],
        "planning_reasons": [
            build_rule(
                "planning_rule",
                f"planning_{index}",
                text,
                component="menu_planning",
            )
            for index, text in enumerate(planning_texts)
        ],
        "menu_reasons": menu_reasons
        if menu_reasons is not None
        else [
            {
                "reason_type": "nutrition_summary",
                "nutrition_score": 13,
                "max_score": 16,
                "nutrient_details": [],
                "sources": [
                    {
                        "component": "menu_planning",
                        "paths": ["nutrition_score"],
                    }
                ],
                "text": NUTRITION_TEXT,
            }
        ],
    }
    confirmation = build_confirmation_state()
    confirmation["planning_context"] = {
        "meal_period": "早餐",
        "meal_period_source": "explicit",
        "diner_count": 1,
        "diner_count_source": "default",
        "total_dish_count": 1,
        "total_dish_count_source": "default",
    }
    return build_generation_result(
        "recommended",
        confirmation_state=confirmation,
        recommendation_reason_result=reason_result,
    )


def assert_four_sections(answer: str) -> None:
    headings = ("菜单", "筛选依据", "规划依据", "营养结果")
    positions = [answer.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert all(answer.count(heading) == 1 for heading in headings)


def test_推荐成功按四段固定顺序组装() -> None:
    answer = AnswerComposerService().compose(build_profile_25_result())

    assert_four_sections(answer)
    assert "早餐" in answer
    assert "1人份" in answer
    assert "南瓜发糕" in answer
    for text in FILTERING_TEXTS + PLANNING_TEXTS + (NUTRITION_TEXT,):
        assert answer.count(text) == 1


def test_各段只消费对应结构化理由() -> None:
    answer = AnswerComposerService().compose(build_profile_25_result())
    menu_at = answer.index("菜单")
    filtering_at = answer.index("筛选依据")
    planning_at = answer.index("规划依据")
    nutrition_at = answer.index("营养结果")

    assert menu_at < answer.index("南瓜发糕") < filtering_at
    assert filtering_at < answer.index(FILTERING_TEXTS[0]) < planning_at
    assert planning_at < answer.index(PLANNING_TEXTS[0]) < nutrition_at
    assert nutrition_at < answer.index(NUTRITION_TEXT)


def test_空的可选依据不输出内容但四段标题仍固定存在() -> None:
    result = build_profile_25_result(filtering_texts=(), planning_texts=())

    answer = AnswerComposerService().compose(result)

    assert_four_sections(answer)
    assert FILTERING_TEXTS[0] not in answer
    assert PLANNING_TEXTS[0] not in answer
    assert NUTRITION_TEXT in answer


def test_重复的整桌规则只输出一次() -> None:
    duplicate_filtering = (FILTERING_TEXTS[1], FILTERING_TEXTS[1])
    duplicate_planning = (PLANNING_TEXTS[1], PLANNING_TEXTS[1])

    answer = AnswerComposerService().compose(
        build_profile_25_result(
            filtering_texts=duplicate_filtering,
            planning_texts=duplicate_planning,
        )
    )

    assert answer.count(FILTERING_TEXTS[1]) == 1
    assert answer.count(PLANNING_TEXTS[1]) == 1


def test_档案25早餐示例只陈述实际处理内容() -> None:
    answer = AnswerComposerService().compose(build_profile_25_result())

    assert_four_sections(answer)
    for required in (
        "早餐",
        "海鲜过敏",
        "允许推荐",
        "默认数量规则选择1道菜",
        "同名菜不重复",
        "固定值计算",
        "优先候选范围",
        "营养得分高",
        "已证明最优",
        "8项营养指标",
    ):
        assert required in answer
    for forbidden in ("增肌", "增加体重", "体检指标"):
        assert forbidden not in answer


def test_已应用健康理由位于规划依据且营养摘要只在营养结果() -> None:
    health_text = (
        "考虑高血压需求，本桌菜单规划已将钠摄入上限"
        "作为必须满足的条件。"
    )
    result = build_profile_25_result(
        menu_reasons=[
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
                "text": health_text,
            },
            {
                "reason_type": "nutrition_summary",
                "nutrition_score": 13,
                "max_score": 16,
                "nutrient_details": [],
                "sources": [
                    {
                        "component": "menu_planning",
                        "paths": ["nutrition_score"],
                    }
                ],
                "text": NUTRITION_TEXT,
            },
        ]
    )

    answer = AnswerComposerService().compose(result)

    assert answer.index("规划依据") < answer.index(health_text)
    assert answer.index(health_text) < answer.index("营养结果")
    assert answer.index("营养结果") < answer.index(NUTRITION_TEXT)


def test_回答中的每条依据都逐字来自结构化理由() -> None:
    filtering_marker = "仅用于筛选来源校验的固定句。"
    planning_marker = "仅用于规划来源校验的固定句。"
    result = build_profile_25_result(
        filtering_texts=(filtering_marker,),
        planning_texts=(planning_marker,),
    )

    answer = AnswerComposerService().compose(result)

    assert answer.count(filtering_marker) == 1
    assert answer.count(planning_marker) == 1
    assert "增肌" not in answer
    assert "增加体重" not in answer


def test_非流式接口content为四段且不增加顶层字段() -> None:
    recommendation = FakeRecommendationService(
        results=[build_profile_25_result()]
    )
    confirmation = FakeConfirmationService()
    with build_app(
        recommendation=recommendation,
        confirmation=confirmation,
    ) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "profile_id": 25,
                "messages": [
                    {"role": "user", "content": "帮我安排一人份早餐"}
                ],
                "stream": False,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"id", "object", "choices", "session_id", "status"}
    assert_four_sections(body["choices"][0]["message"]["content"])


def test_流式接口重组后的content仍为四段() -> None:
    recommendation = FakeRecommendationService(
        results=[build_profile_25_result()]
    )
    with build_app(recommendation=recommendation) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "profile_id": 25,
                "messages": [
                    {"role": "user", "content": "帮我安排一人份早餐"}
                ],
                "stream": True,
            },
            headers={"Accept": "text/event-stream"},
        )

    chunks = parse_sse(response)
    content = "".join(
        chunk["choices"][0]["delta"].get("content", "")
        for chunk in chunks
    )
    assert_four_sections(content)
    assert json.loads(response.text.split("data: ")[1].splitlines()[0])
