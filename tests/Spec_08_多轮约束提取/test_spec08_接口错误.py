from __future__ import annotations

from datetime import datetime

from .spec08_support import FakeLLMClient, build_turn_result


def _stub_resolver(production_contract):
    """构造一个合法的Spec_07餐次解析服务(默认午餐窗口时钟)。"""

    def clock() -> datetime:
        return datetime(2026, 8, 14, 12, 0)

    return production_contract.MealPeriodResolutionService(clock=clock)


def _raising_session_factory():
    def factory():
        raise RuntimeError("数据库连接失败")

    return factory


def test_Session工厂抛异常_500(
    production_contract,
    profile_id,
    assert_multi_turn_error,
):
    service = production_contract.MultiTurnConstraintService(
        _raising_session_factory(),
        FakeLLMClient(),
        _stub_resolver(production_contract),
    )
    assert_multi_turn_error(lambda: service.create_session(profile_id), 500)


def test_Session工厂非callable_500(
    production_contract,
    profile_id,
    assert_multi_turn_error,
):
    assert_multi_turn_error(
        lambda: production_contract.MultiTurnConstraintService(
            "不是工厂",
            FakeLLMClient(),
            _stub_resolver(production_contract),
        ),
        500,
    )


def test_LLM提取器非callable_500(
    production_contract,
    session_factory,
    assert_multi_turn_error,
):
    assert_multi_turn_error(
        lambda: production_contract.MultiTurnConstraintService(
            session_factory,
            "不是提取器",
            _stub_resolver(production_contract),
        ),
        500,
    )


def test_餐次解析服务缺失_500(
    production_contract,
    session_factory,
    assert_multi_turn_error,
):
    assert_multi_turn_error(
        lambda: production_contract.MultiTurnConstraintService(
            session_factory,
            FakeLLMClient(),
            None,
        ),
        500,
    )


def test_LLM超时_503(build_service, session_factory, profile_id, assert_multi_turn_error):
    llm_client = FakeLLMClient(error=TimeoutError("请求超时"))
    service = build_service(session_factory, llm_client)
    session_id = service.create_session(profile_id)
    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "今晚吃啥"),
        503,
    )


def test_LLM连接失败_503(build_service, session_factory, profile_id, assert_multi_turn_error):
    llm_client = FakeLLMClient(error=ConnectionError("连接失败"))
    service = build_service(session_factory, llm_client)
    session_id = service.create_session(profile_id)
    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "今晚吃啥"),
        503,
    )


def test_LLM返回非对象_502_重试一次仍失败(
    build_service,
    session_factory,
    profile_id,
    assert_multi_turn_error,
):
    llm_client = FakeLLMClient(responses=["一段文本", "一段文本"])
    service = build_service(session_factory, llm_client)
    session_id = service.create_session(profile_id)
    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "今晚吃啥"),
        502,
    )
    assert llm_client.call_count == 2


def test_LLM输出缺字段_502(
    build_service,
    session_factory,
    profile_id,
    assert_multi_turn_error,
):
    llm_client = FakeLLMClient(
        responses=[
            {"dialogue_id": 1, "meal_periods": []},
            {"dialogue_id": 1, "meal_periods": []},
        ]
    )
    service = build_service(session_factory, llm_client)
    session_id = service.create_session(profile_id)
    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "今晚吃啥"),
        502,
    )


def test_LLM输出dialogue_id不匹配_502(
    build_service,
    session_factory,
    profile_id,
    assert_multi_turn_error,
):
    llm_client = FakeLLMClient(
        responses=[
            build_turn_result(999999),
            build_turn_result(999999),
        ]
    )
    service = build_service(session_factory, llm_client)
    session_id = service.create_session(profile_id)
    assert_multi_turn_error(
        lambda: service.submit_turn(session_id, "今晚吃啥"),
        502,
    )
    assert llm_client.call_count == 2
