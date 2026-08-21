from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app

from .conftest import (
    FakeChatModel,
    FakeConfirmationService,
    FakeDependencyError,
    FakeRecommendationService,
    build_generation_result,
)


def build_app(
    confirmation: FakeConfirmationService | None = None,
    recommendation: FakeRecommendationService | None = None,
    chat_model: FakeChatModel | None = None,
) -> TestClient:
    services = SimpleNamespace(
        confirmation=confirmation or FakeConfirmationService(),
        recommendation=recommendation or FakeRecommendationService(),
    )
    return TestClient(create_app(services=services, chat_model=chat_model))


def parse_sse(response: Any) -> list[dict[str, Any]]:
    """解析SSE响应体为块列表。"""

    lines = [
        line
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    return [json.loads(line[6:]) for line in lines]


def test_健康检查返回200() -> None:
    with build_app() as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_创建会话成功返回会话号() -> None:
    with build_app() as client:
        response = client.post("/v1/sessions", json={"profile_id": 25})

    assert response.status_code == 201
    assert response.json() == {"session_id": 101}


def test_创建会话profile_id非整数返回400() -> None:
    with build_app() as client:
        response = client.post("/v1/sessions", json={"profile_id": "abc"})

    assert response.status_code == 400


@pytest.mark.parametrize("profile_id", [0, 51])
def test_创建会话profile_id越界返回400(profile_id: int) -> None:
    with build_app() as client:
        response = client.post(
            "/v1/sessions",
            json={"profile_id": profile_id},
        )

    assert response.status_code == 400


def test_创建会话profile_id缺失返回400() -> None:
    with build_app() as client:
        response = client.post("/v1/sessions", json={})

    assert response.status_code == 400


def test_创建会话档案不存在返回404() -> None:
    confirmation = FakeConfirmationService(
        error=FakeDependencyError(404, "用户档案不存在")
    )
    with build_app(confirmation=confirmation) as client:
        response = client.post("/v1/sessions", json={"profile_id": 25})

    assert response.status_code == 404


def test_首轮带档案自动建会话() -> None:
    confirmation = FakeConfirmationService()
    with build_app(confirmation=confirmation) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "profile_id": 25,
                "messages": [{"role": "user", "content": "帮我安排晚饭"}],
            },
        )

    assert response.status_code == 200
    assert confirmation.created == [25]
    body = response.json()
    assert body["session_id"] == 101
    assert body["status"] == "recommended"
    assert body["choices"][0]["message"]["content"].strip()


def test_多轮带会话号继续不重复创建() -> None:
    confirmation = FakeConfirmationService()
    with build_app(confirmation=confirmation) as client:
        client.post(
            "/v1/chat/completions",
            json={
                "profile_id": 25,
                "messages": [{"role": "user", "content": "帮我安排晚饭"}],
            },
        )
        response = client.post(
            "/v1/chat/completions",
            json={
                "session_id": 101,
                "messages": [{"role": "user", "content": "别做辣的"}],
            },
        )

    assert response.status_code == 200
    assert confirmation.created == [25]
    assert confirmation.submitted == [
        (101, "帮我安排晚饭"),
        (101, "别做辣的"),
    ]


def test_同时缺失档案与会话号返回400() -> None:
    with build_app() as client:
        response = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "帮我安排晚饭"}]},
        )

    assert response.status_code == 400


def test_同时提供档案与会话号以会话号为准() -> None:
    confirmation = FakeConfirmationService()
    with build_app(confirmation=confirmation) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "profile_id": 25,
                "session_id": 101,
                "messages": [{"role": "user", "content": "帮我安排晚饭"}],
            },
        )

    assert response.status_code == 200
    assert confirmation.created == []
    assert confirmation.submitted == [(101, "帮我安排晚饭")]


def test_会话不存在返回404() -> None:
    confirmation = FakeConfirmationService(
        error=FakeDependencyError(404, "会话不存在")
    )
    with build_app(confirmation=confirmation) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "session_id": 999,
                "messages": [{"role": "user", "content": "帮我安排晚饭"}],
            },
        )

    assert response.status_code == 404


def test_消息为空数组返回400() -> None:
    with build_app() as client:
        response = client.post(
            "/v1/chat/completions",
            json={"profile_id": 25, "messages": []},
        )

    assert response.status_code == 400


def test_最后一条非用户消息返回400() -> None:
    with build_app() as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "profile_id": 25,
                "messages": [
                    {"role": "user", "content": "帮我安排晚饭"},
                    {"role": "assistant", "content": "好的"},
                ],
            },
        )

    assert response.status_code == 400


def test_非流式响应结构完整() -> None:
    with build_app() as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "profile_id": 25,
                "messages": [{"role": "user", "content": "帮我安排晚饭"}],
            },
        )

    body = response.json()
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["message"]["content"].strip()
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["session_id"] == 101
    assert body["status"] == "recommended"


def test_流式按块输出且带会话号头() -> None:
    with build_app() as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "profile_id": 25,
                "messages": [{"role": "user", "content": "帮我安排晚饭"}],
                "stream": True,
            },
            headers={"Accept": "text/event-stream"},
        )

    assert response.status_code == 200
    assert response.headers["x-session-id"] == "101"
    chunks = parse_sse(response)
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    content_chunks = [
        chunk
        for chunk in chunks
        if chunk["choices"][0]["delta"].get("content")
    ]
    assert content_chunks
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_流式短回答至少输出一个文本块() -> None:
    recommendation = FakeRecommendationService(
        results=[build_generation_result("in_progress")]
    )
    with build_app(recommendation=recommendation) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "profile_id": 25,
                "messages": [{"role": "user", "content": "帮我安排晚饭"}],
                "stream": True,
            },
            headers={"Accept": "text/event-stream"},
        )

    chunks = parse_sse(response)
    content_chunks = [
        chunk
        for chunk in chunks
        if chunk["choices"][0]["delta"].get("content")
    ]
    assert content_chunks
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_业务异常映射为OpenAI风格错误体() -> None:
    recommendation = FakeRecommendationService(
        error=FakeDependencyError(503, "LLM服务请求超时或不可用")
    )
    with build_app(recommendation=recommendation) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "profile_id": 25,
                "messages": [{"role": "user", "content": "帮我安排晚饭"}],
            },
        )

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["message"]
    assert error["type"]
    assert error["code"] == 503


def test_polish开启时回答经LLM润色() -> None:
    chat_model = FakeChatModel("润色后的回答：番茄炒蛋和清蒸鲈鱼都很不错。")
    with build_app(chat_model=chat_model) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "profile_id": 25,
                "messages": [{"role": "user", "content": "帮我安排晚饭"}],
                "polish": True,
            },
        )

    assert response.status_code == 200
    assert chat_model.prompts
    content = response.json()["choices"][0]["message"]["content"]
    assert content == "润色后的回答：番茄炒蛋和清蒸鲈鱼都很不错。"


def test_polish缺省走模板不调用润色() -> None:
    chat_model = FakeChatModel("润色后的回答：番茄炒蛋和清蒸鲈鱼都很不错。")
    with build_app(chat_model=chat_model) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "profile_id": 25,
                "messages": [{"role": "user", "content": "帮我安排晚饭"}],
            },
        )

    assert response.status_code == 200
    assert chat_model.prompts == []
    content = response.json()["choices"][0]["message"]["content"]
    assert "已为您安排" in content
