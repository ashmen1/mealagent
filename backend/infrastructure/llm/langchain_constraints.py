from __future__ import annotations

import os

from backend.core.dialogue_constraint_contract import (
    CONSTRAINT_OUTPUT_SCHEMA,
    DialogueConstraintExtractionError,
)


class LangChainConstraintExtractor:
    """使用LangChain ChatModel结构化输出能力的约束提取适配器。"""

    def __init__(self, chat_model: object) -> None:
        with_structured_output = getattr(
            chat_model,
            "with_structured_output",
            None,
        )
        if not callable(with_structured_output):
            raise DialogueConstraintExtractionError(
                500,
                "LLM运行配置缺失或ChatModel无效",
            )

        try:
            structured_model = with_structured_output(
                CONSTRAINT_OUTPUT_SCHEMA
            )
        except Exception as exc:
            raise DialogueConstraintExtractionError(
                500,
                "无法创建LLM结构化输出配置",
            ) from exc

        if not callable(getattr(structured_model, "invoke", None)):
            raise DialogueConstraintExtractionError(
                500,
                "LangChain结构化输出Runnable无效",
            )
        self._structured_model = structured_model

    def __call__(self, prompt: str) -> object:
        try:
            return self._structured_model.invoke(prompt)
        except (TimeoutError, ConnectionError) as exc:
            raise DialogueConstraintExtractionError(
                503,
                "LLM服务请求超时或连接失败",
            ) from exc
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            if status_code in {401, 403, 408, 429} or (
                type(status_code) is int and status_code >= 500
            ):
                raise DialogueConstraintExtractionError(
                    503,
                    "LLM认证、限流或Provider服务失败",
                ) from exc
            raise


def create_langchain_constraint_extractor_from_environment(
) -> LangChainConstraintExtractor:
    """使用运行环境创建DeepSeek Anthropic兼容接口的真实提取器。"""

    base_url = _read_required_environment_variable("ANTHROPIC_BASE_URL")
    auth_token = _read_required_environment_variable("ANTHROPIC_AUTH_TOKEN")
    model_name = _read_required_environment_variable("ANTHROPIC_MODEL")

    try:
        from langchain_anthropic import ChatAnthropic

        chat_model = ChatAnthropic(
            model=model_name,
            base_url=base_url,
            api_key=auth_token,
            temperature=0,
            timeout=60,
            max_retries=0,
            thinking={"type": "disabled"},
        )
    except ImportError as exc:
        raise DialogueConstraintExtractionError(
            500,
            "缺少langchain-anthropic运行依赖",
        ) from exc
    except Exception as exc:
        raise DialogueConstraintExtractionError(
            500,
            "无法创建DeepSeek LangChain ChatModel",
        ) from exc

    return LangChainConstraintExtractor(chat_model)


def _read_required_environment_variable(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise DialogueConstraintExtractionError(
            500,
            f"缺少LLM运行配置：{name}",
        )
    return value.strip()


__all__ = [
    "CONSTRAINT_OUTPUT_SCHEMA",
    "LangChainConstraintExtractor",
    "create_langchain_constraint_extractor_from_environment",
]
