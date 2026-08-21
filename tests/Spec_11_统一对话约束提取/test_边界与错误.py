from __future__ import annotations

import copy

import pytest

from .spec11_support import (
    FakeLLMClient,
    build_dish,
    build_turn_result,
)


@pytest.mark.parametrize("profile_id", [0, -1, True, "1", None])
def test_create_session_profile_id非法返回400且不调用LLM(
    profile_id,
    build_service,
    session_factory,
    assert_dialogue_error,
):
    llm_client = FakeLLMClient()
    service = build_service(session_factory, llm_client)

    assert_dialogue_error(lambda: service.create_session(profile_id), 400)
    assert llm_client.call_count == 0


def test_create_session_档案不存在返回409且不调用LLM(
    build_service,
    session_factory,
    assert_dialogue_error,
):
    llm_client = FakeLLMClient()
    service = build_service(session_factory, llm_client)

    assert_dialogue_error(lambda: service.create_session(999999), 409)
    assert llm_client.call_count == 0


@pytest.mark.parametrize("session_id", [0, -1, True, "1", None])
def test_submit_turn_session_id非法返回400(
    session_id,
    build_service,
    session_factory,
    assert_dialogue_error,
):
    service = build_service(session_factory, FakeLLMClient())
    assert_dialogue_error(
        lambda: service.submit_turn(session_id, "今晚吃啥"),
        400,
    )


@pytest.mark.parametrize("message", ["", "   ", 1, None, []])
def test_submit_turn空或非字符串消息返回400且不调用LLM(
    message,
    start_session,
    assert_dialogue_error,
):
    service, llm_client, session_id = start_session()

    assert_dialogue_error(
        lambda: service.submit_turn(session_id, message),
        400,
    )
    assert llm_client.call_count == 0


def test_submit_turn_会话不存在返回400且不调用LLM(
    start_session,
    assert_dialogue_error,
):
    service, llm_client, session_id = start_session()

    assert_dialogue_error(
        lambda: service.submit_turn(session_id + 999, "今晚吃啥"),
        400,
    )
    assert llm_client.call_count == 0


def test_get_session_会话不存在返回400(
    start_session,
    assert_dialogue_error,
):
    service, _, session_id = start_session()
    assert_dialogue_error(lambda: service.get_session(session_id + 999), 400)


def test_Session工厂失败返回500(
    production_contract,
    profile_id,
    assert_dialogue_error,
    clock_at,
):
    def broken_factory():
        raise RuntimeError("数据库连接失败")

    resolver = production_contract.MealPeriodResolutionService(
        clock=clock_at(12, 0)
    )
    service = production_contract.DialogueConstraintService(
        broken_factory,
        FakeLLMClient(),
        resolver,
    )

    assert_dialogue_error(lambda: service.create_session(profile_id), 500)


def test_LLM超时和连接失败返回503(
    build_service,
    session_factory,
    profile_id,
    assert_dialogue_error,
):
    for error in (TimeoutError("超时"), ConnectionError("连接失败")):
        service = build_service(
            session_factory,
            FakeLLMClient(error=error),
        )
        session_id = service.create_session(profile_id)
        assert_dialogue_error(
            lambda: service.submit_turn(session_id, "今晚吃啥"),
            503,
        )


def test_LLM非对象重试一次后返回502(
    build_service,
    session_factory,
    profile_id,
    assert_dialogue_error,
):
    llm_client = FakeLLMClient(responses=["文本", "仍是文本"])
    service = build_service(session_factory, llm_client)
    session_id = service.create_session(profile_id)

    assert_dialogue_error(
        lambda: service.submit_turn(session_id, "今晚吃啥"),
        502,
    )
    assert llm_client.call_count == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dialogue_id", 0),
        ("diner_count", 0),
        ("diner_count", True),
        ("total_dish_count", -1),
        ("max_total_time_minutes", "30"),
        ("max_difficulty", "复杂"),
        ("meal_periods", ["晚餐", "晚餐"]),
        ("meal_periods", ["夜宵"]),
        ("available_ingredients", ["番茄", "番茄"]),
    ],
)
def test_顶层字段类型范围枚举和重复非法均返回502(
    field,
    value,
    start_session,
    assert_dialogue_error,
):
    service, llm_client, session_id = start_session()
    invalid = build_turn_result(session_id)
    invalid[field] = value
    if field == "meal_periods" and value:
        invalid["evidence"] = {
            f"meal_periods[{index}]": str(item)
            for index, item in enumerate(value)
        }
    if field == "available_ingredients" and value:
        invalid["evidence"] = {
            f"available_ingredients[{index}]": str(item)
            for index, item in enumerate(value)
        }
    if field in {
        "diner_count",
        "total_dish_count",
        "max_total_time_minutes",
        "max_difficulty",
    } and value is not None:
        invalid["evidence"] = {field: str(value)}
    llm_client.responses = [invalid, copy.deepcopy(invalid)]

    assert_dialogue_error(
        lambda: service.submit_turn(session_id, str(value)),
        502,
    )


def test_正整数最小值和无上限大值合法(start_session):
    service, llm_client, session_id = start_session()
    llm_client.response = build_turn_result(
        session_id,
        diner_count=1,
        total_dish_count=1,
        max_total_time_minutes=999999,
        dishes=[build_dish(count=1, dish_type="菜")],
        evidence={
            "diner_count": "1人",
            "total_dish_count": "1道",
            "max_total_time_minutes": "999999分钟",
            "dishes[0].count": "1道",
            "dishes[0].dish_type": "菜",
        },
    )

    result = service.submit_turn(
        session_id,
        "1人吃1道菜，999999分钟内完成",
    )

    merged = result["merged_constraints"]
    assert merged["diner_count"] == 1
    assert merged["total_dish_count"] == 1
    assert merged["max_total_time_minutes"] == 999999


@pytest.mark.parametrize(
    ("dish_overrides", "message"),
    [
        ({"count": 0}, "零道菜"),
        ({"dish_type": "甜品"}, "甜品"),
        ({"taste_preferences": {"is_hot": True}}, "要热的"),
        ({"taste_preferences": {"is_spicy": 1}}, "要辣的"),
        ({"cuisines": ["粤菜", "粤菜"]}, "粤菜"),
        ({"effects": ["治疗感冒"]}, "治疗感冒"),
        ({"special_populations": ["青年"]}, "青年"),
    ],
)
def test_Dish字段范围枚举和重复非法返回502(
    dish_overrides,
    message,
    start_session,
    assert_dialogue_error,
):
    service, llm_client, session_id = start_session()
    invalid = build_turn_result(
        session_id,
        dishes=[build_dish(**dish_overrides)],
        evidence={},
    )
    llm_client.responses = [invalid, copy.deepcopy(invalid)]

    assert_dialogue_error(
        lambda: service.submit_turn(session_id, message),
        502,
    )


@pytest.mark.parametrize(
    ("total", "dishes"),
    [
        (1, [build_dish(), build_dish(dish_type="汤")]),
        (4, [build_dish(count=1), build_dish(count=1, dish_type="汤")]),
    ],
)
def test_菜品总数与组数量冲突返回502(
    total,
    dishes,
    start_session,
    assert_dialogue_error,
):
    service, llm_client, session_id = start_session()
    evidence: dict[str, str] = {"total_dish_count": "总数"}
    for index, dish in enumerate(dishes):
        if dish["count"] is not None:
            evidence[f"dishes[{index}].count"] = "一道"
        if dish["dish_type"] != "未指定":
            evidence[f"dishes[{index}].dish_type"] = "汤"
    invalid = build_turn_result(
        session_id,
        total_dish_count=total,
        dishes=dishes,
        evidence=evidence,
    )
    llm_client.responses = [invalid, copy.deepcopy(invalid)]

    assert_dialogue_error(
        lambda: service.submit_turn(session_id, "总数四道，一道菜一道汤"),
        502,
    )


@pytest.mark.parametrize(
    ("meal_periods", "expected_status"),
    [
        (["早餐"], "ready_for_planning"),
        (["午餐"], "ready_for_planning"),
        (["晚餐"], "ready_for_planning"),
        (["下午茶"], "needs_confirmation"),
        (["早餐", "午餐"], "needs_confirmation"),
    ],
)
def test_明确餐次状态判定(
    meal_periods,
    expected_status,
    start_session,
):
    service, llm_client, session_id = start_session()
    message = "和".join(meal_periods)
    llm_client.response = build_turn_result(
        session_id,
        meal_periods=meal_periods,
        evidence={
            f"meal_periods[{index}]": period
            for index, period in enumerate(meal_periods)
        },
    )

    result = service.submit_turn(session_id, message)

    assert result["status"] == expected_status


@pytest.mark.parametrize(
    ("hour", "minute", "expected_status"),
    [
        (5, 0, "ready_for_planning"),
        (10, 0, "ready_for_planning"),
        (10, 1, "needs_confirmation"),
        (11, 0, "ready_for_planning"),
        (14, 0, "ready_for_planning"),
        (14, 1, "needs_confirmation"),
        (17, 0, "ready_for_planning"),
        (21, 0, "ready_for_planning"),
        (21, 1, "needs_confirmation"),
    ],
)
def test_未明确餐次按上海时间窗口含端点判定(
    hour,
    minute,
    expected_status,
    start_session,
    clock_at,
):
    service, llm_client, session_id = start_session(
        clock=clock_at(hour, minute)
    )
    llm_client.response = build_turn_result(session_id)

    result = service.submit_turn(session_id, "吃什么")

    assert result["status"] == expected_status


def test_missing_requirements顺序固定但不阻止规划(start_session):
    service, llm_client, session_id = start_session()
    llm_client.response = build_turn_result(
        session_id,
        meal_periods=["晚餐"],
        evidence={"meal_periods[0]": "晚餐"},
    )

    result = service.submit_turn(session_id, "晚餐")

    assert result["status"] == "ready_for_planning"
    assert result["missing_requirements"] == ["人数", "明确菜品类型"]


def test_首轮dishes为空数组返回502(
    start_session,
    assert_dialogue_error,
):
    service, llm_client, session_id = start_session()
    invalid = build_turn_result(session_id, dishes=[])
    llm_client.responses = [invalid, copy.deepcopy(invalid)]

    assert_dialogue_error(
        lambda: service.submit_turn(session_id, "帮我安排晚饭"),
        502,
    )


def test_可用食材非标准食材名返回502(
    start_session,
    assert_dialogue_error,
):
    service, llm_client, session_id = start_session()
    invalid = build_turn_result(
        session_id,
        available_ingredients=["西红柿"],
        evidence={"available_ingredients[0]": "西红柿"},
    )
    llm_client.responses = [invalid, copy.deepcopy(invalid)]

    assert_dialogue_error(
        lambda: service.submit_turn(session_id, "家里有西红柿"),
        502,
    )


def test_变更声明field与dish_index同时非空返回502(
    start_session,
    assert_dialogue_error,
):
    service, llm_client, session_id = start_session()
    invalid = build_turn_result(
        session_id,
        change_actions=[
            {
                "field": "diner_count",
                "dish_index": 0,
                "action": "replace",
                "evidence": "两个人",
            }
        ],
    )
    llm_client.responses = [invalid, copy.deepcopy(invalid)]

    assert_dialogue_error(
        lambda: service.submit_turn(session_id, "两个人吃"),
        502,
    )


def test_首轮证据包含多余路径返回502(
    start_session,
    assert_dialogue_error,
):
    service, llm_client, session_id = start_session()
    invalid = build_turn_result(
        session_id,
        evidence={"meal_periods[0]": "晚饭"},
    )
    llm_client.responses = [invalid, copy.deepcopy(invalid)]

    assert_dialogue_error(
        lambda: service.submit_turn(session_id, "帮我安排晚饭"),
        502,
    )


def test_证据片段不是用户原文连续子串返回502(
    start_session,
    assert_dialogue_error,
):
    service, llm_client, session_id = start_session()
    invalid = build_turn_result(
        session_id,
        diner_count=2,
        evidence={"diner_count": "不是原文"},
    )
    llm_client.responses = [invalid, copy.deepcopy(invalid)]

    assert_dialogue_error(
        lambda: service.submit_turn(session_id, "帮我安排晚饭"),
        502,
    )
