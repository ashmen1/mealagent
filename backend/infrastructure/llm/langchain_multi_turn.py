from __future__ import annotations

from backend.core.multi_turn_contract import (
    MULTI_TURN_OUTPUT_SCHEMA,
    MultiTurnConstraintError,
)


class LangChainMultiTurnExtractor:
    """使用LangChain ChatModel结构化输出能力的多轮约束提取适配器。"""

    def __init__(self, chat_model: object) -> None:
        with_structured_output = getattr(
            chat_model,
            "with_structured_output",
            None,
        )
        if not callable(with_structured_output):
            raise MultiTurnConstraintError(
                500,
                "LLM运行配置缺失或ChatModel无效",
            )

        try:
            # 统一走工具调用协议:阿里百炼等兼容接口不支持 json_schema 响应格式
            structured_model = with_structured_output(
                MULTI_TURN_OUTPUT_SCHEMA,
                method="function_calling",
            )
        except Exception as exc:
            raise MultiTurnConstraintError(
                500,
                "无法创建LLM结构化输出配置",
            ) from exc

        if not callable(getattr(structured_model, "invoke", None)):
            raise MultiTurnConstraintError(
                500,
                "LangChain结构化输出Runnable无效",
            )
        self._structured_model = structured_model

    def __call__(self, prompt: str) -> object:
        try:
            return self._structured_model.invoke(prompt)
        except (TimeoutError, ConnectionError) as exc:
            raise MultiTurnConstraintError(
                503,
                "LLM服务请求超时或连接失败",
            ) from exc
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            if status_code in {401, 403, 408, 429} or (
                type(status_code) is int and status_code >= 500
            ):
                raise MultiTurnConstraintError(
                    503,
                    "LLM认证、限流或Provider服务失败",
                ) from exc
            raise


def create_langchain_multi_turn_extractor_from_environment(
) -> LangChainMultiTurnExtractor:
    """使用运行环境创建多轮约束提取器,Provider由环境变量选择。"""

    from .langchain_constraints import create_chat_model_from_environment

    chat_model = create_chat_model_from_environment()
    return LangChainMultiTurnExtractor(chat_model)


__all__ = [
    "MULTI_TURN_OUTPUT_SCHEMA",
    "LangChainMultiTurnExtractor",
    "create_langchain_multi_turn_extractor_from_environment",
]
