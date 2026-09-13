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
    health: object | None = None,
    api_token: str | None = None,
) -> TestClient:
    services = SimpleNamespace(
        confirmation=confirmation or FakeConfirmationService(),
        recommendation=recommendation or FakeRecommendationService(),
        health=health,
    )
    return TestClient(
        create_app(
            services=services,
            chat_model=chat_model,
            api_token=api_token,
        )
    )


def parse_sse(response: Any) -> list[dict[str, Any]]:
    """解析SSE响应体为块列表。"""

    lines = [
        line
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    return [json.loads(line[6:]) for line in lines]


def test_存活检查返回200且不访问依赖() -> None:
    with build_app() as client:
        response = client.get("/health/live")

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


def test_创建会话档案不存在返回409() -> None:
    confirmation = FakeConfirmationService(
        error=FakeDependencyError(409, "用户档案不存在")
    )
    with build_app(confirmation=confirmation) as client:
        response = client.post("/v1/sessions", json={"profile_id": 25})

    assert response.status_code == 409


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


def test_会话不存在返回400() -> None:
    confirmation = FakeConfirmationService(
        error=FakeDependencyError(400, "会话不存在")
    )
    with build_app(confirmation=confirmation) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "session_id": 999,
                "messages": [{"role": "user", "content": "帮我安排晚饭"}],
            },
        )

    assert response.status_code == 400


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


def test_启用鉴权时未提供访问密钥返回401() -> None:
    confirmation = FakeConfirmationService()
    with build_app(confirmation=confirmation, api_token="secret") as client:
        response = client.post("/v1/sessions", json={"profile_id": 25})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == 401
    assert confirmation.created == []


def test_启用鉴权时访问密钥错误返回401() -> None:
    confirmation = FakeConfirmationService()
    with build_app(confirmation=confirmation, api_token="secret") as client:
        response = client.post(
            "/v1/sessions",
            json={"profile_id": 25},
            headers={"Authorization": "Bearer wrong"},
        )

    assert response.status_code == 401
    assert confirmation.created == []


def test_启用鉴权时非Bearer方案返回401() -> None:
    with build_app(api_token="secret") as client:
        response = client.post(
            "/v1/sessions",
            json={"profile_id": 25},
            headers={"Authorization": "Basic secret"},
        )

    assert response.status_code == 401


def test_启用鉴权时访问密钥正确放行() -> None:
    confirmation = FakeConfirmationService()
    with build_app(confirmation=confirmation, api_token="secret") as client:
        response = client.post(
            "/v1/sessions",
            json={"profile_id": 25},
            headers={"Authorization": "Bearer secret"},
        )

    assert response.status_code == 201
    assert response.json() == {"session_id": 101}
    assert confirmation.created == [25]


def test_启用鉴权时对话路由未带密钥不进入业务链路() -> None:
    confirmation = FakeConfirmationService()
    recommendation = FakeRecommendationService()
    with build_app(
        confirmation=confirmation,
        recommendation=recommendation,
        api_token="secret",
    ) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "profile_id": 25,
                "messages": [{"role": "user", "content": "帮我安排晚饭"}],
            },
        )

    assert response.status_code == 401
    assert confirmation.created == []
    assert recommendation.generated == []


def test_启用鉴权时对话路由带正确密钥正常回答() -> None:
    with build_app(api_token="secret") as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "profile_id": 25,
                "messages": [{"role": "user", "content": "帮我安排晚饭"}],
            },
            headers={"Authorization": "Bearer secret"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "recommended"


def test_存活检查不受鉴权影响() -> None:
    with build_app(api_token="secret") as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_未启用鉴权时不带密钥可访问() -> None:
    with build_app() as client:
        response = client.post("/v1/sessions", json={"profile_id": 25})

    assert response.status_code == 201


def test_流式响应带防缓冲响应头() -> None:
    with build_app(api_token="secret") as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "profile_id": 25,
                "messages": [{"role": "user", "content": "帮我安排晚饭"}],
                "stream": True,
            },
            headers={
                "Accept": "text/event-stream",
                "Authorization": "Bearer secret",
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["connection"] == "keep-alive"
    assert response.headers["x-session-id"] == "101"
