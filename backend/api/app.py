from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from backend.application import create_constraint_services
from backend.infrastructure.llm.langchain_constraints import (
    create_chat_model_from_environment,
)
from backend.services.answer_composer import (
    AnswerComposerService,
    compose_with_llm,
)

STREAM_CHUNK_CHARS = 24


class ApiBusinessError(Exception):
    """业务异常的统一API错误，携带HTTP状态码。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class CreateSessionRequest(BaseModel):
    profile_id: int = Field(ge=1, le=50, description="用户档案ID")


class ChatRequest(BaseModel):
    model: str | None = None
    messages: list[dict[str, Any]]
    stream: bool = False
    session_id: int | None = Field(default=None, gt=0)
    profile_id: int | None = Field(default=None, ge=1, le=50)
    polish: bool = Field(
        default=False,
        description="true时用LLM润色回答文本，缺省模板组装",
    )


def create_app(
    services: object | None = None,
    chat_model: object | None = None,
) -> FastAPI:
    """创建对外HTTP服务；services为None时由lifespan创建真实容器。

    chat_model 用于 polish=true 的LLM润色；未注入时在首次润色请求时
    从环境创建（惰性，避免缺LLM配置时影响模板路径）。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = services
        app.state.owns_container = container is None
        if container is None:
            container = create_constraint_services()
        app.state.services = container
        app.state.composer = AnswerComposerService()
        app.state.chat_model = chat_model
        yield
        if app.state.owns_container:
            container.close()

    app = FastAPI(title="个性化膳食规划Agent", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return _error_response(400, "请求参数不合法")

    @app.exception_handler(ApiBusinessError)
    async def handle_business_error(
        request: Request,
        exc: ApiBusinessError,
    ) -> JSONResponse:
        return _error_response(exc.status_code, exc.message)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _error_response(500, f"服务器内部错误：{exc}")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/sessions", status_code=201)
    def create_session(
        request: Request,
        payload: CreateSessionRequest,
    ) -> dict[str, int]:
        services = request.app.state.services
        session_id = _call(
            services.confirmation.create_session,
            payload.profile_id,
        )
        if type(session_id) is not int or session_id <= 0:
            raise ApiBusinessError(500, "会话创建结果无效")
        return {"session_id": session_id}

    @app.post("/v1/chat/completions")
    def chat_completions(request: Request, payload: ChatRequest) -> Any:
        services = request.app.state.services
        message = _last_user_message(payload.messages)

        session_id = payload.session_id
        if session_id is None:
            if payload.profile_id is None:
                raise ApiBusinessError(
                    400,
                    "缺少profile_id与session_id，请至少提供一个",
                )
            session_id = _call(
                services.confirmation.create_session,
                payload.profile_id,
            )
            if type(session_id) is not int or session_id <= 0:
                raise ApiBusinessError(500, "会话创建结果无效")

        _call(services.confirmation.submit_turn, session_id, message)
        result = _call(services.recommendation.generate, session_id)
        if not isinstance(result, dict):
            raise ApiBusinessError(500, "推荐结果无效")
        status = result.get("status")
        if not isinstance(status, str):
            raise ApiBusinessError(500, "推荐状态无效")
        if payload.polish:
            answer = _polish_answer(request.app, result)
        else:
            answer = request.app.state.composer.compose(result)

        if payload.stream:
            return StreamingResponse(
                _stream_answer(answer),
                media_type="text/event-stream",
                headers={"X-Session-Id": str(session_id)},
            )
        return {
            "id": f"chatcmpl-{session_id}",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": answer},
                    "finish_reason": "stop",
                }
            ],
            "session_id": session_id,
            "status": status,
        }

    return app


def _polish_answer(app: FastAPI, result: dict[str, Any]) -> str:
    """用LLM润色回答文本；菜名缺失时compose_with_llm回退模板。"""

    chat_model = app.state.chat_model
    if chat_model is None:
        chat_model = create_chat_model_from_environment()
        app.state.chat_model = chat_model
    return compose_with_llm(chat_model, result)


def _last_user_message(messages: list[dict[str, Any]]) -> str:
    """取最后一条非空user消息；否则400。"""

    if not messages:
        raise ApiBusinessError(400, "messages不能为空")
    last = messages[-1]
    if last.get("role") != "user":
        raise ApiBusinessError(400, "最后一条消息必须是user")
    content = last.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ApiBusinessError(400, "user消息内容必须是非空字符串")
    return content


def _call(action: Callable[..., object], *args: object) -> object:
    """执行依赖调用并映射带状态码的业务异常。"""

    try:
        return action(*args)
    except ApiBusinessError:
        raise
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and 400 <= status <= 599:
            raise ApiBusinessError(status, str(exc)) from exc
        raise ApiBusinessError(500, str(exc)) from exc


def _stream_answer(answer: str) -> Iterator[str]:
    """把回答文本切成OpenAI兼容的SSE块序列。"""

    yield _sse_chunk({"role": "assistant"})
    for start in range(0, len(answer), STREAM_CHUNK_CHARS):
        yield _sse_chunk({"content": answer[start : start + STREAM_CHUNK_CHARS]})
    yield _sse_chunk({}, "stop")


def _sse_chunk(
    delta: dict[str, Any],
    finish_reason: str | None = None,
) -> str:
    payload = {
        "choices": [
            {"index": 0, "delta": delta, "finish_reason": finish_reason}
        ]
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": (
                    "invalid_request_error"
                    if status_code < 500
                    else "api_error"
                ),
                "code": status_code,
            }
        },
    )


__all__ = [
    "ApiBusinessError",
    "ChatRequest",
    "CreateSessionRequest",
    "create_app",
]
