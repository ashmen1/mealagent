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
    可选 LLM_*_BACKUP 系列备用配置：主模型配额耗尽(429)时自动切换。
    """

    base_url = _read_required_environment_variable("LLM_BASE_URL")
    auth_token = _read_required_environment_variable("LLM_AUTH_TOKEN")
    model_name = _read_required_environment_variable("LLM_MODEL")
    provider = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()
    chat_model = _create_chat_model(provider, base_url, auth_token, model_name)
    backup_model = _create_backup_chat_model_from_environment()
    if backup_model is None:
        return chat_model
    return _FallbackChatModel(chat_model, backup_model)


def _create_backup_chat_model_from_environment() -> object | None:
    """从 LLM_*_BACKUP 环境变量创建备用模型；未配置任何备用项时返回 None。"""

    base_url = os.environ.get("LLM_BASE_URL_BACKUP", "").strip()
    auth_token = os.environ.get("LLM_AUTH_TOKEN_BACKUP", "").strip()
    model_name = os.environ.get("LLM_MODEL_BACKUP", "").strip()
    provider = os.environ.get("LLM_PROVIDER_BACKUP", "openai").strip().lower()
    if not (base_url and auth_token and model_name):
        return None
    return _create_chat_model(provider, base_url, auth_token, model_name)


class _FallbackChatModel:
    """主备双模型包装：主模型配额耗尽(429)时自动切换到备用模型重试。"""

    def __init__(self, primary: object, backup: object | None = None) -> None:
        self._primary = primary
        self._backup = backup

    def with_structured_output(
        self, schema: dict[str, Any], **kwargs: Any
    ) -> "_FallbackChatModel":
        primary_structured = self._primary.with_structured_output(
            schema, **kwargs
        )
        backup_structured = None
        if self._backup is not None:
            backup_structured = self._backup.with_structured_output(
                schema, **kwargs
            )
        return _FallbackChatModel(primary_structured, backup_structured)

    def invoke(self, prompt: str) -> Any:
        try:
            return self._primary.invoke(prompt)
        except Exception as exc:
            if self._backup is not None and _is_quota_exhausted(exc):
                return self._backup.invoke(prompt)
            raise


def _is_quota_exhausted(exc: Exception) -> bool:
    """判断异常是否为配额耗尽：HTTP 429 或额度类错误文本。"""

    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return True
    message = str(exc).lower()
    return "insufficient_quota" in message or (
        "quota" in message and "exhausted" in message
    )


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
            # 阿里百炼等兼容接口用 enable_thinking 控制思考；
            # DeepSeek 兼容接口忽略该参数且默认开启思考，需显式传 thinking 对象
            if "deepseek" in base_url.lower():
                extra_body = {
                    "thinking": {"type": "enabled" if enable_thinking else "disabled"}
                }
            else:
                extra_body = {"enable_thinking": enable_thinking}
            return ChatOpenAI(
                model=model_name,
                base_url=base_url,
                api_key=auth_token,
                temperature=0,
                timeout=60,
                max_retries=0,
                extra_body=extra_body,
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
