from __future__ import annotations

import inspect
from copy import deepcopy
from typing import Any

import pytest

from .spec10_support import (
    build_candidate,
    build_filtering_result,
    build_nutrient_grades,
    build_planning_result,
    build_selected_dish,
)


def build_effective_dish(**overrides: Any) -> dict[str, Any]:
    dish: dict[str, Any] = {
        "count": None,
        "dish_type": "未指定",
        "taste_preferences": {},
        "cuisines": [],
        "effects": [],
        "special_populations": [],
        "required_ingredient_groups": [],
    }
    dish.update(deepcopy(overrides))
    return dish


def build_decision_context(
    *,
    candidate_attempts: list[dict[str, Any]] | None = None,
    dishes: list[dict[str, Any]] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    effective_constraints: dict[str, Any] = {
        "profile_id": 25,
        "dialogue_id": 101,
        "meal_periods": ["早餐"],
        "diner_count": 1,
        "total_dish_count": None,
        "max_total_time_minutes": None,
        "max_difficulty": None,
        "available_ingredients": [],
        "allergens": [],
        "dishes": dishes or [build_effective_dish()],
        "has_conflicts": False,
        "conflicts": [],
    }
    effective_constraints.update(deepcopy(overrides))
    return {
        "effective_constraints": effective_constraints,
        "candidate_attempts": deepcopy(
            candidate_attempts
            if candidate_attempts is not None
            else [
                {
                    "candidate_limit": None,
                    "candidate_counts": [1],
                    "outcome": "accepted",
                    "nutrition_score": 9,
                }
            ]
        ),
    }


def invoke_new_contract(
    production_contract: Any,
    filtering_result: dict[str, Any],
    planning_result: dict[str, Any],
    decision_context: object,
) -> dict[str, Any]:
    """兼容红阶段旧签名，使各行为测试能暴露具体缺失字段。"""

    service = production_contract.RecommendationReasonService()
    parameters = inspect.signature(service.build).parameters
    if "decision_context" not in parameters:
        return service.build(filtering_result, planning_result)
    return service.build(
        filtering_result,
        planning_result,
        decision_context,
    )


def build_breakfast_filtering(
    *names: str,
    matched_tags: list[str] | None = None,
) -> dict[str, Any]:
    tags = ["早餐"] if matched_tags is None else matched_tags
    groups = ["餐次"] if tags else []
    return build_filtering_result(
        dishes=[
            [build_candidate(name, tags, groups)]
            for name in (names or ("南瓜发糕",))
        ]
    )


def build_selected_breakfast(*names: str, **overrides: Any) -> dict[str, Any]:
    selected = [
        build_selected_dish(index, name)
        for index, name in enumerate(names or ("南瓜发糕",))
    ]
    overrides.setdefault("meal_period", "早餐")
    overrides.setdefault("diner_count", 1)
    return build_planning_result(selected_dishes=selected, **overrides)


def build_many_breakfast_candidates(count: int) -> dict[str, Any]:
    candidates = [
        build_candidate("南瓜发糕", ["早餐"], ["餐次"])
    ]
    candidates.extend(
        build_candidate(f"候选菜{index:03d}", [], [])
        for index in range(1, count)
    )
    return build_filtering_result(dishes=[candidates])


def test_build公开接口接收推荐决策上下文(production_contract) -> None:
    parameters = list(
        inspect.signature(
            production_contract.RecommendationReasonService.build
        ).parameters
    )

    assert parameters == [
        "self",
        "dish_filtering_result",
        "menu_planning_result",
        "decision_context",
    ]


def test_正常路径返回结构化筛选与规划理由(production_contract) -> None:
    result = invoke_new_contract(
        production_contract,
        build_breakfast_filtering(),
        build_selected_breakfast(),
        build_decision_context(),
    )

    assert result["filtering_reasons"]
    assert result["planning_reasons"]
    for reason in result["filtering_reasons"]:
        assert reason["reason_type"] == "filtering_rule"
        assert isinstance(reason["rule"], str) and reason["rule"]
        assert isinstance(reason["details"], dict)
        assert isinstance(reason["affected_recipe_names"], list)
        assert isinstance(reason["dish_constraint_indexes"], list)
        assert reason["sources"]
        assert isinstance(reason["text"], str) and reason["text"]
        for source in reason["sources"]:
            assert source["component"] in {
                "constraint_integration",
                "dish_filtering",
                "menu_planning",
                "menu_recommendation",
            }
            assert isinstance(source["paths"], list) and source["paths"]
    for reason in result["planning_reasons"]:
        assert reason["reason_type"] == "planning_rule"
        assert isinstance(reason["rule"], str) and reason["rule"]
        assert isinstance(reason["details"], dict)
        assert isinstance(reason["affected_recipe_names"], list)
        assert isinstance(reason["dish_constraint_indexes"], list)
        assert reason["sources"]
        assert isinstance(reason["text"], str) and reason["text"]
        for source in reason["sources"]:
            assert source["component"] in {
                "constraint_integration",
                "dish_filtering",
                "menu_planning",
                "menu_recommendation",
            }
            assert isinstance(source["paths"], list) and source["paths"]


def test_五类正向标签都形成实际筛选依据(production_contract) -> None:
    tags = ["早餐", "清淡", "粤菜", "助眠", "儿童"]
    filtering = build_filtering_result(
        dishes=[
            [
                build_candidate(
                    "南瓜发糕",
                    tags,
                    ["餐次", "口味", "菜系", "功效", "人群"],
                )
            ]
        ]
    )
    context = build_decision_context(
        dishes=[
            build_effective_dish(
                taste_preferences={"is_light": True},
                cuisines=["粤菜"],
                effects=["助眠"],
                special_populations=["儿童"],
            )
        ]
    )

    result = invoke_new_contract(
        production_contract,
        filtering,
        build_selected_breakfast(),
        context,
    )

    tag_reasons = result["filtering_reasons"][:5]
    assert [
        next(tag for tag in tags if tag in str(reason["details"]))
        for reason in tag_reasons
    ] == tags
    assert all(
        reason["affected_recipe_names"] == ["南瓜发糕"]
        for reason in tag_reasons
    )


def test_全部实际筛选规则按固定顺序输出(production_contract) -> None:
    effective_dish = build_effective_dish(
        dish_type="菜",
        taste_preferences={"is_spicy": False},
        required_ingredient_groups=[
            {
                "match": "all",
                "items": [
                    {"kind": "ingredient", "value": "番茄"},
                    {"kind": "category", "value": "蛋类"},
                ],
            },
            {
                "match": "any",
                "items": [
                    {"kind": "ingredient", "value": "鸡蛋"},
                    {"kind": "concept", "value": "面"},
                ],
            },
        ],
    )
    context = build_decision_context(
        dishes=[effective_dish],
        max_total_time_minutes=30,
        max_difficulty="中等",
        available_ingredients=["番茄", "鸡蛋"],
        allergens=["海鲜"],
    )

    result = invoke_new_contract(
        production_contract,
        build_breakfast_filtering(),
        build_selected_breakfast(),
        context,
    )

    texts = [reason["text"] for reason in result["filtering_reasons"]]
    assert "早餐" in texts[0]
    assert "辣" in texts[1] and "排除" in texts[1]
    assert "菜" in texts[2] and "限定" in texts[2]
    assert "30" in texts[3] and "上限" in texts[3]
    assert "中等" in texts[4] and "上限" in texts[4]
    assert all(token in texts[5] for token in ("番茄", "蛋类", "鸡蛋", "面"))
    assert all(token in texts[5] for token in ("全部", "任一", "同时"))
    assert all(token in texts[6] for token in ("番茄", "鸡蛋", "核心食材"))
    assert texts[7] == (
        "已按档案中的海鲜过敏信息，在候选筛选阶段"
        "排除含相关标准食材的菜谱。"
    )
    assert texts[8] == "本次只从允许推荐的菜谱中选择。"
    assert texts[9] == (
        "符合条件的候选先按标签命中数从多到少排列，"
        "命中数相同时按菜名稳定排序。"
    )


def test_空筛选条件不输出但常驻规则每次输出(production_contract) -> None:
    result = invoke_new_contract(
        production_contract,
        build_breakfast_filtering(matched_tags=[]),
        build_selected_breakfast(),
        build_decision_context(meal_periods=[]),
    )

    assert [item["text"] for item in result["filtering_reasons"]] == [
        "本次只从允许推荐的菜谱中选择。",
        (
            "符合条件的候选先按标签命中数从多到少排列，"
            "命中数相同时按菜名稳定排序。"
        ),
    ]


def test_相同标签组合影响多道菜时合并为整桌理由(
    production_contract,
) -> None:
    names = ("南瓜发糕", "小米粥")
    result = invoke_new_contract(
        production_contract,
        build_breakfast_filtering(*names),
        build_selected_breakfast(*names),
        build_decision_context(
            dishes=[
                build_effective_dish(),
                build_effective_dish(count=1),
            ],
            candidate_attempts=[
                {
                    "candidate_limit": None,
                    "candidate_counts": [1, 1],
                    "outcome": "accepted",
                    "nutrition_score": 9,
                }
            ],
        ),
    )

    breakfast_reasons = [
        item
        for item in result["filtering_reasons"]
        if "早餐" in str(item["details"])
    ]
    assert len(breakfast_reasons) == 1
    assert breakfast_reasons[0]["affected_recipe_names"] == list(names)
    assert breakfast_reasons[0]["dish_constraint_indexes"] == [0, 1]


def test_不同标签组合不得错误合并(production_contract) -> None:
    filtering = build_filtering_result(
        dishes=[
            [build_candidate("南瓜发糕", ["早餐"], ["餐次"])],
            [build_candidate("白灼芥蓝", ["清淡"], ["口味"])],
        ]
    )
    result = invoke_new_contract(
        production_contract,
        filtering,
        build_selected_breakfast("南瓜发糕", "白灼芥蓝"),
        build_decision_context(
            dishes=[
                build_effective_dish(),
                build_effective_dish(
                    taste_preferences={"is_light": True}
                ),
            ],
            candidate_attempts=[
                {
                    "candidate_limit": None,
                    "candidate_counts": [1, 1],
                    "outcome": "accepted",
                    "nutrition_score": 9,
                }
            ],
        ),
    )

    tag_reasons = [
        item
        for item in result["filtering_reasons"]
        if any(token in str(item["details"]) for token in ("早餐", "清淡"))
    ]
    assert len(tag_reasons) == 2
    assert [item["affected_recipe_names"] for item in tag_reasons] == [
        ["南瓜发糕"],
        ["白灼芥蓝"],
    ]


def test_过敏理由只使用原始过敏项且不作额外安全承诺(
    production_contract,
) -> None:
    context = build_decision_context(allergens=["海鲜"])
    context["effective_constraints"]["health_goals"] = ["增肌", "增加体重"]
    context["effective_constraints"]["medical_metrics"] = {
        "体脂率": "偏高"
    }
    result = invoke_new_contract(
        production_contract,
        build_breakfast_filtering(),
        build_selected_breakfast(),
        context,
    )

    rendered = str(result)
    assert (
        "已按档案中的海鲜过敏信息，在候选筛选阶段"
        "排除含相关标准食材的菜谱。"
    ) in rendered
    for forbidden in (
        "基围虾",
        "大闸蟹",
        "治愈",
        "绝对安全",
        "不存在交叉污染",
        "增肌",
        "增加体重",
        "体脂率",
    ):
        assert forbidden not in rendered


def test_整桌过敏规则影响多道菜时只生成一条理由(
    production_contract,
) -> None:
    names = ("南瓜发糕", "小米粥")
    result = invoke_new_contract(
        production_contract,
        build_breakfast_filtering(*names),
        build_selected_breakfast(*names),
        build_decision_context(
            dishes=[
                build_effective_dish(),
                build_effective_dish(count=1),
            ],
            allergens=["海鲜"],
            candidate_attempts=[
                {
                    "candidate_limit": None,
                    "candidate_counts": [1, 1],
                    "outcome": "accepted",
                    "nutrition_score": 9,
                }
            ],
        ),
    )

    allergy_reasons = [
        reason
        for reason in result["filtering_reasons"]
        if reason["text"].startswith("已按档案中的海鲜过敏信息")
    ]
    assert len(allergy_reasons) == 1
    assert allergy_reasons[0]["affected_recipe_names"] == list(names)
    assert allergy_reasons[0]["dish_constraint_indexes"] == [0, 1]


@pytest.mark.parametrize(
    "context,expected_tokens,forbidden_tokens",
    [
        (
            build_decision_context(total_dish_count=2, diner_count=2),
            ("明确", "2道菜"),
            (),
        ),
        (
            build_decision_context(
                dishes=[
                    build_effective_dish(count=1),
                    build_effective_dish(count=None),
                ],
                candidate_attempts=[
                    {
                        "candidate_limit": None,
                        "candidate_counts": [1, 1],
                        "outcome": "accepted",
                        "nutrition_score": 9,
                    }
                ],
            ),
            ("1道", "至少选择一道"),
            ("平均",),
        ),
        (
            build_decision_context(diner_count=1, total_dish_count=None),
            ("1人", "默认", "1道菜"),
            (),
        ),
    ],
)
def test_菜品数量理由区分显式分组与人数默认规则(
    production_contract,
    context,
    expected_tokens,
    forbidden_tokens,
) -> None:
    names = (
        ("南瓜发糕", "小米粥")
        if len(context["effective_constraints"]["dishes"]) == 2
        or context["effective_constraints"]["total_dish_count"] == 2
        else ("南瓜发糕",)
    )
    if len(names) == 2 and len(context["effective_constraints"]["dishes"]) == 1:
        context["effective_constraints"]["dishes"] = [
            build_effective_dish(count=2)
        ]
        context["candidate_attempts"][0]["candidate_counts"] = [2]
        filtering = build_filtering_result(
            dishes=[[build_candidate(name, ["早餐"], ["餐次"]) for name in names]]
        )
        planning = build_planning_result(
            selected_dishes=[build_selected_dish(0, name) for name in names],
            meal_period="早餐",
            diner_count=context["effective_constraints"]["diner_count"],
        )
    else:
        filtering = build_breakfast_filtering(*names)
        planning = build_selected_breakfast(*names)

    result = invoke_new_contract(
        production_contract,
        filtering,
        planning,
        context,
    )

    count_text = result["planning_reasons"][0]["text"]
    assert all(token in count_text for token in expected_tokens)
    assert all(token not in count_text for token in forbidden_tokens)


def test_规划理由固定顺序和固定文案(production_contract) -> None:
    result = invoke_new_contract(
        production_contract,
        build_breakfast_filtering(),
        build_selected_breakfast(),
        build_decision_context(),
    )

    texts = [reason["text"] for reason in result["planning_reasons"]]
    assert "1人" in texts[0] and "默认" in texts[0]
    assert "同名菜不重复" in texts[1]
    assert (
        "菜谱配方和整份营养按库中固定值计算，"
        "本次不调整食材克重"
    ) in texts[1]
    assert texts[2] == "本次在优先候选范围内找到达到营养目标的可行菜单。"
    assert texts[3] == (
        "在满足约束的菜单中，依次按营养得分高、正常区间外营养项少、"
        "标签命中多、候选顺序靠前进行选择。"
    )
    assert texts[4] == "本次返回的是在上述规则下已证明最优的菜单。"


@pytest.mark.parametrize(
    "filtering,attempts,planning_overrides,expected_text",
    [
        (
            build_many_breakfast_candidates(150),
            [
                {
                    "candidate_limit": 100,
                    "candidate_counts": [100],
                    "outcome": "accepted",
                    "nutrition_score": 9,
                }
            ],
            {},
            "本次在优先候选范围内找到达到营养目标的可行菜单。",
        ),
        (
            build_many_breakfast_candidates(350),
            [
                {
                    "candidate_limit": 100,
                    "candidate_counts": [100],
                    "outcome": "below_target",
                    "nutrition_score": 7,
                },
                {
                    "candidate_limit": 300,
                    "candidate_counts": [300],
                    "outcome": "accepted",
                    "nutrition_score": 9,
                },
            ],
            {},
            "本次扩大候选范围后找到可行菜单。",
        ),
        (
            build_many_breakfast_candidates(350),
            [
                {
                    "candidate_limit": 100,
                    "candidate_counts": [100],
                    "outcome": "below_target",
                    "nutrition_score": 6,
                },
                {
                    "candidate_limit": 300,
                    "candidate_counts": [300],
                    "outcome": "below_target",
                    "nutrition_score": 7,
                },
                {
                    "candidate_limit": None,
                    "candidate_counts": [350],
                    "outcome": "accepted",
                    "nutrition_score": 7,
                },
            ],
            {
                "nutrient_grades": build_nutrient_grades(
                    {
                        "energy_kcal": "excellent",
                        "protein_g": "excellent",
                        "fat_g": "excellent",
                        "carbohydrate_g": "normal",
                        "fiber_g": "bad",
                        "sodium_mg": "bad",
                        "calcium_mg": "bad",
                        "iron_mg": "bad",
                    }
                ),
                "nutrition_score": 7,
            },
            "本次使用全量候选得到可行菜单，营养得分未达到8分目标。",
        ),
    ],
)
def test_候选阶段只输出实际结果且隐藏内部数量(
    production_contract,
    filtering,
    attempts,
    planning_overrides,
    expected_text,
) -> None:
    result = invoke_new_contract(
        production_contract,
        filtering,
        build_selected_breakfast(**planning_overrides),
        build_decision_context(candidate_attempts=attempts),
    )

    stage_text = result["planning_reasons"][2]["text"]
    assert stage_text == expected_text
    assert "100" not in stage_text
    assert "300" not in stage_text


@pytest.mark.parametrize(
    "score,grades,attempt",
    [
        (
            0,
            {name: "bad" for name in (
                "energy_kcal",
                "protein_g",
                "fat_g",
                "carbohydrate_g",
                "fiber_g",
                "sodium_mg",
                "calcium_mg",
                "iron_mg",
            )},
            {
                "candidate_limit": None,
                "candidate_counts": [1],
                "outcome": "accepted",
                "nutrition_score": 0,
            },
        ),
        (
            16,
            {name: "excellent" for name in (
                "energy_kcal",
                "protein_g",
                "fat_g",
                "carbohydrate_g",
                "fiber_g",
                "sodium_mg",
                "calcium_mg",
                "iron_mg",
            )},
            {
                "candidate_limit": None,
                "candidate_counts": [1],
                "outcome": "accepted",
                "nutrition_score": 16,
            },
        ),
    ],
)
def test_候选阶段接受营养得分极值(
    production_contract,
    score,
    grades,
    attempt,
) -> None:
    result = invoke_new_contract(
        production_contract,
        build_breakfast_filtering(),
        build_selected_breakfast(
            nutrient_grades=build_nutrient_grades(grades),
            nutrition_score=score,
        ),
        build_decision_context(candidate_attempts=[attempt]),
    )

    assert result["planning_reasons"][2]["details"]["nutrition_score"] == score


def test_健康硬约束只在规划依据展示一次且不解释未应用字段(
    production_contract,
) -> None:
    context = build_decision_context()
    context["effective_constraints"]["health_goals"] = ["增肌", "增加体重"]
    context["effective_constraints"]["medical_metrics"] = {"血脂": "偏高"}
    result = invoke_new_contract(
        production_contract,
        build_breakfast_filtering(),
        build_selected_breakfast(applied_health_constraints=["高血压"]),
        context,
    )

    planning_text = "\n".join(
        reason["text"] for reason in result["planning_reasons"]
    )
    menu_text = "\n".join(reason["text"] for reason in result["menu_reasons"])
    rendered = planning_text + "\n" + menu_text
    assert "高血压" not in planning_text
    assert rendered.count("高血压") == 1
    assert "增肌" not in rendered
    assert "增加体重" not in rendered
    assert "血脂" not in rendered


@pytest.mark.parametrize(
    "bad_context",
    [
        None,
        [],
        {},
        {"effective_constraints": {}, "candidate_attempts": []},
        {"effective_constraints": None, "candidate_attempts": []},
        {"effective_constraints": {}, "candidate_attempts": None},
    ],
)
def test_推荐决策上下文缺失或结构非法返回400(
    production_contract,
    bad_context,
) -> None:
    with pytest.raises(Exception) as captured:
        invoke_new_contract(
            production_contract,
            build_breakfast_filtering(),
            build_selected_breakfast(),
            bad_context,
        )

    assert getattr(captured.value, "status_code", None) == 400


@pytest.mark.parametrize("candidate_limit", [-1, 0, 99, 200, True, "100"])
def test_候选阶段数量非法返回400(
    production_contract,
    candidate_limit,
) -> None:
    context = build_decision_context()
    context["candidate_attempts"][0]["candidate_limit"] = candidate_limit

    with pytest.raises(Exception) as captured:
        invoke_new_contract(
            production_contract,
            build_breakfast_filtering(),
            build_selected_breakfast(),
            context,
        )

    assert getattr(captured.value, "status_code", None) == 400


def test_重复候选阶段返回400(production_contract) -> None:
    attempt = {
        "candidate_limit": 100,
        "candidate_counts": [1],
        "outcome": "below_target",
        "nutrition_score": 7,
    }
    context = build_decision_context(candidate_attempts=[attempt, attempt])

    with pytest.raises(Exception) as captured:
        invoke_new_contract(
            production_contract,
            build_breakfast_filtering(),
            build_selected_breakfast(),
            context,
        )

    assert getattr(captured.value, "status_code", None) == 400


@pytest.mark.parametrize(
    "mutate_context",
    [
        lambda value: value["candidate_attempts"][0].update(
            nutrition_score=8
        ),
        lambda value: value["effective_constraints"].update(profile_id=99),
        lambda value: value["effective_constraints"].update(dialogue_id=999),
    ],
)
def test_决策上下文与最终规划冲突返回500(
    production_contract,
    mutate_context,
) -> None:
    context = build_decision_context()
    mutate_context(context)

    with pytest.raises(Exception) as captured:
        invoke_new_contract(
            production_contract,
            build_breakfast_filtering(),
            build_selected_breakfast(),
            context,
        )

    assert getattr(captured.value, "status_code", None) == 500


def test_三个输入均不修改且重复调用结果确定(production_contract) -> None:
    filtering = build_breakfast_filtering()
    planning = build_selected_breakfast()
    context = build_decision_context()
    snapshots = deepcopy((filtering, planning, context))

    first = invoke_new_contract(
        production_contract,
        filtering,
        planning,
        context,
    )
    second = invoke_new_contract(
        production_contract,
        filtering,
        planning,
        context,
    )

    assert (filtering, planning, context) == snapshots
    assert first == second
    assert first["filtering_reasons"]
    assert first["planning_reasons"]
