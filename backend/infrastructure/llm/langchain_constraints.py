from __future__ import annotations

import copy
import os
from typing import Any

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
            # 统一走工具调用协议：阿里百炼等兼容接口不支持 json_schema 响应格式
            structured_model = with_structured_output(
                CONSTRAINT_OUTPUT_SCHEMA,
                method="function_calling",
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
            result = self._structured_model.invoke(prompt)
            return _normalize_tool_integer_fields(result)
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


def _normalize_tool_integer_fields(result: object) -> object:
    """归一化兼容接口把工具整数参数序列化为十进制字符串的差异。"""

    if not isinstance(result, dict):
        return result
    normalized = copy.deepcopy(result)
    for field in (
        "dialogue_id",
        "diner_count",
        "total_dish_count",
        "max_total_time_minutes",
    ):
        if field in normalized:
            normalized[field] = _normalize_decimal_integer(normalized[field])

    dishes = normalized.get("dishes")
    if isinstance(dishes, list):
        for dish in dishes:
            if isinstance(dish, dict) and "count" in dish:
                dish["count"] = _normalize_decimal_integer(dish["count"])

    actions = normalized.get("change_actions")
    if isinstance(actions, list):
        for action in actions:
            if isinstance(action, dict) and "dish_index" in action:
                action["dish_index"] = _normalize_decimal_integer(
                    action["dish_index"]
                )
    return normalized


def _normalize_decimal_integer(value: object) -> object:
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return value


def build_lowest_reasoning_config() -> dict[str, Any]:
    """返回 DeepSeek 关闭思考并请求最低推理强度的统一配置。"""

    return {
        "thinking": {"type": "disabled"},
        "reasoning_effort": "low",
    }


def create_langchain_constraint_extractor_from_environment(
) -> LangChainConstraintExtractor:
    """使用运行环境创建真实LLM提取器，Provider由环境变量选择。"""

    chat_model = create_chat_model_from_environment()
    return LangChainConstraintExtractor(chat_model)


def create_chat_model_from_environment() -> object:
    """使用运行环境创建真实LLM ChatModel，Provider由环境变量选择。

    环境变量：LLM_PROVIDER 选择协议（anthropic/openai），
    LLM_BASE_URL、LLM_AUTH_TOKEN、LLM_MODEL 为连接与模型配置。
    """

    base_url = _read_required_environment_variable("LLM_BASE_URL")
    auth_token = _read_required_environment_variable("LLM_AUTH_TOKEN")
    model_name = _read_required_environment_variable("LLM_MODEL")
    provider = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()
    return _create_chat_model(provider, base_url, auth_token, model_name)


def _create_chat_model(
    provider: str,
    base_url: str,
    auth_token: str,
    model_name: str,
) -> object:
    """按 Provider 创建 LangChain ChatModel；Provider 由配置决定。"""

    if provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(
                model=model_name,
                base_url=base_url,
                api_key=auth_token,
                temperature=0,
                timeout=60,
                max_retries=0,
                **build_lowest_reasoning_config(),
            )
        except ImportError as exc:
            raise DialogueConstraintExtractionError(
                500,
                "缺少langchain-anthropic运行依赖",
            ) from exc
        except Exception as exc:
            raise DialogueConstraintExtractionError(
                500,
                f"无法创建{provider} LangChain ChatModel",
            ) from exc

    if provider == "openai":
        try:
            from langchain_openai import ChatOpenAI

            # 约束提取是固定结构任务，默认关闭思考（非推理模式）最快；
            # 需要思考时通过 LLM_ENABLE_THINKING=true 打开
            enable_thinking = (
                os.environ.get("LLM_ENABLE_THINKING", "false")
                .strip()
                .lower()
                == "true"
            )
            return ChatOpenAI(
                model=model_name,
                base_url=base_url,
                api_key=auth_token,
                temperature=0,
                timeout=60,
                max_retries=0,
                extra_body={"enable_thinking": enable_thinking},
            )
        except ImportError as exc:
            raise DialogueConstraintExtractionError(
                500,
                "缺少langchain-openai运行依赖",
            ) from exc
        except Exception as exc:
            raise DialogueConstraintExtractionError(
                500,
                f"无法创建{provider} LangChain ChatModel",
            ) from exc

    raise DialogueConstraintExtractionError(
        500,
        f"不支持的LLM Provider：{provider}",
    )


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
    "build_lowest_reasoning_config",
    "create_chat_model_from_environment",
    "create_langchain_constraint_extractor_from_environment",
]
