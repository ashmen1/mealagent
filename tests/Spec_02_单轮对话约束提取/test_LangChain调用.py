from __future__ import annotations

import copy
from typing import Any

import pytest

from spec02_support import (
    build_empty_result,
    ingredient_session,
    invoke_extract,
    production_contract,
)


VALID_DIALOGUE = {
    "id": 1,
    "turn_count": 1,
    "user_messages": ["今晚吃啥比较好？"],
}


class ProviderHTTPError(Exception):
    """模拟LangChain底层Provider返回的HTTP错误。"""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"Provider HTTP {status_code}")
        self.status_code = status_code


class FakeStructuredRunnable:
    """模拟ChatModel.with_structured_output返回的Runnable。"""

    def __init__(
        self,
        response: object = None,
        error: BaseException | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.inputs: list[str] = []

    @property
    def call_count(self) -> int:
        return len(self.inputs)

    def invoke(self, prompt: str) -> object:
        self.inputs.append(prompt)
        if self.error is not None:
            raise self.error
        return self.response


class FakeChatModel:
    """只模拟LangChain ChatModel的结构化输出入口。"""

    def __init__(
        self,
        runnable: FakeStructuredRunnable | None = None,
        setup_error: BaseException | None = None,
    ) -> None:
        self.runnable = runnable or FakeStructuredRunnable()
        self.setup_error = setup_error
        self.schemas: list[object] = []

    def with_structured_output(
        self,
        schema: object,
        method: str | None = None,
    ):
        del method
        self.schemas.append(schema)
        if self.setup_error is not None:
            raise self.setup_error
        return self.runnable


def get_json_schema(schema: object) -> dict[str, Any]:
    if isinstance(schema, dict):
        return schema
    model_json_schema = getattr(schema, "model_json_schema", None)
    assert callable(model_json_schema), "结构化输出Schema必须可转换为JSON Schema"
    return model_json_schema()


def resolve_schema_node(
    root_schema: dict[str, Any],
    node: dict[str, Any],
) -> dict[str, Any]:
    reference = node.get("$ref")
    if not reference:
        return node
    prefix = "#/$defs/"
    assert reference.startswith(prefix)
    return root_schema["$defs"][reference.removeprefix(prefix)]


def test_LangChain结构化输出正常路径只调用一次真实模型(
    production_contract,
    invoke_extract,
):
    expected = build_empty_result()
    runnable = FakeStructuredRunnable(response=expected)
    chat_model = FakeChatModel(runnable=runnable)
    extractor = production_contract.LangChainConstraintExtractor(chat_model)

    result = invoke_extract(copy.deepcopy(VALID_DIALOGUE), extractor)

    assert result == expected
    assert len(chat_model.schemas) == 1
    assert runnable.call_count == 1
    assert VALID_DIALOGUE["user_messages"][0] in runnable.inputs[0]


def test_LangChain结构化输出Schema完整且禁止未声明字段(
    production_contract,
):
    chat_model = FakeChatModel()

    production_contract.LangChainConstraintExtractor(chat_model)

    schema = get_json_schema(chat_model.schemas[0])
    assert schema.get("additionalProperties") is False
    assert set(schema["required"]) == {
        "dialogue_id",
        "meal_periods",
        "diner_count",
        "max_total_time_minutes",
        "available_ingredients",
        "dishes",
        "evidence",
    }
    dish_schema = resolve_schema_node(
        schema,
        schema["properties"]["dishes"]["items"],
    )
    assert dish_schema.get("additionalProperties") is False
    assert set(dish_schema["required"]) == {
        "count",
        "dish_type",
        "taste_preferences",
        "cuisines",
        "effects",
        "special_populations",
        "required_ingredients",
    }
    requirement_schema = resolve_schema_node(
        schema,
        dish_schema["properties"]["required_ingredients"]["items"],
    )
    assert requirement_schema.get("additionalProperties") is False
    assert set(requirement_schema["required"]) == {"kind", "value"}


@pytest.mark.parametrize("bad_model", [None, object()])
def test_LangChain配置缺失或类型错误返回500(
    bad_model,
    production_contract,
):
    with pytest.raises(
        production_contract.DialogueConstraintExtractionError
    ) as captured:
        production_contract.LangChainConstraintExtractor(bad_model)

    assert captured.value.status_code == 500


def test_模型不支持结构化输出时返回500(production_contract):
    chat_model = FakeChatModel(
        setup_error=NotImplementedError("不支持结构化输出")
    )

    with pytest.raises(
        production_contract.DialogueConstraintExtractionError
    ) as captured:
        production_contract.LangChainConstraintExtractor(chat_model)

    assert captured.value.status_code == 500
    assert len(chat_model.schemas) == 1


def test_真实模型工厂缺少任一环境变量返回500(
    monkeypatch,
    production_contract,
):
    for variable_name in (
        "LLM_BASE_URL",
        "LLM_AUTH_TOKEN",
        "LLM_MODEL",
    ):
        monkeypatch.delenv(variable_name, raising=False)

    with pytest.raises(
        production_contract.DialogueConstraintExtractionError
    ) as captured:
        production_contract.create_langchain_constraint_extractor_from_environment()

    assert captured.value.status_code == 500


def test_DeepSeek关闭思考并请求最低effort(production_contract):
    module = __import__(
        "backend.infrastructure.llm.langchain_constraints",
        fromlist=["build_lowest_reasoning_config", "_create_chat_model"],
    )

    assert module.build_lowest_reasoning_config() == {
        "thinking": {"type": "disabled"},
        "reasoning_effort": "low",
    }

    chat_model = module._create_chat_model(
        "anthropic",
        "http://example.com",
        "token",
        "deepseek-v4-flash",
    )
    payload = chat_model._get_request_payload("测试")
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["output_config"]["effort"] == "low"


def test_Provider由环境变量选择_不支持的返回500(
    monkeypatch,
    production_contract,
):
    monkeypatch.setenv("LLM_BASE_URL", "http://example.com")
    monkeypatch.setenv("LLM_AUTH_TOKEN", "token")
    monkeypatch.setenv("LLM_MODEL", "model-x")
    monkeypatch.setenv("LLM_PROVIDER", "unsupported-provider")

    with pytest.raises(
        production_contract.DialogueConstraintExtractionError
    ) as captured:
        production_contract.create_langchain_constraint_extractor_from_environment()

    assert captured.value.status_code == 500
    assert "不支持的LLM Provider" in str(captured.value)


def test_openai_Provider创建ChatOpenAI并传入配置(
    monkeypatch,
    production_contract,
):
    monkeypatch.setenv("LLM_BASE_URL", "http://example.com")
    monkeypatch.setenv("LLM_AUTH_TOKEN", "token")
    monkeypatch.setenv("LLM_MODEL", "model-x")
    monkeypatch.setenv("LLM_PROVIDER", "openai")

    captured_kwargs: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured_kwargs.update(kwargs)

        def with_structured_output(
            self,
            schema: object,
            method: str | None = None,
        ) -> "FakeChatOpenAI":
            del schema, method
            return self

        def invoke(self, prompt: object) -> None:
            del prompt
            raise NotImplementedError

    monkeypatch.setattr(
        "langchain_openai.ChatOpenAI",
        FakeChatOpenAI,
    )

    extractor = (
        production_contract.create_langchain_constraint_extractor_from_environment()
    )

    assert extractor is not None
    assert captured_kwargs["model"] == "model-x"
    assert captured_kwargs["base_url"] == "http://example.com"
    assert captured_kwargs["api_key"] == "token"
    # DeepSeek 系列默认关闭思考，非推理模式最快
    assert captured_kwargs["extra_body"] == {"enable_thinking": False}


@pytest.mark.parametrize(
    "service_error",
    [
        TimeoutError("请求超时"),
        ConnectionError("连接失败"),
        ProviderHTTPError(401),
        ProviderHTTPError(429),
        ProviderHTTPError(500),
    ],
    ids=["超时", "连接失败", "认证失败", "限流", "服务不可用"],
)
def test_LangChain服务失败返回503且不重试(
    service_error,
    production_contract,
    invoke_extract,
):
    runnable = FakeStructuredRunnable(error=service_error)
    extractor = production_contract.LangChainConstraintExtractor(
        FakeChatModel(runnable=runnable)
    )

    with pytest.raises(
        production_contract.DialogueConstraintExtractionError
    ) as captured:
        invoke_extract(copy.deepcopy(VALID_DIALOGUE), extractor)

    assert captured.value.status_code == 503
    assert runnable.call_count == 1


@pytest.mark.parametrize(
    "bad_response",
    [None, "{}", "```json\n{}\n```", []],
    ids=["空结果", "JSON文本", "Markdown文本", "数组"],
)
def test_LangChain结构化输出失败返回502且不降级文本解析(
    bad_response,
    production_contract,
    invoke_extract,
):
    runnable = FakeStructuredRunnable(response=bad_response)
    extractor = production_contract.LangChainConstraintExtractor(
        FakeChatModel(runnable=runnable)
    )

    with pytest.raises(
        production_contract.DialogueConstraintExtractionError
    ) as captured:
        invoke_extract(copy.deepcopy(VALID_DIALOGUE), extractor)

    assert captured.value.status_code == 502
    # 502 响应校验失败会重试一次，再次失败才抛出
    assert runnable.call_count == 2


def test_LangChain请求和结果不做缓存(
    production_contract,
    invoke_extract,
):
    runnable = FakeStructuredRunnable(response=build_empty_result())
    extractor = production_contract.LangChainConstraintExtractor(
        FakeChatModel(runnable=runnable)
    )

    invoke_extract(copy.deepcopy(VALID_DIALOGUE), extractor)
    invoke_extract(copy.deepcopy(VALID_DIALOGUE), extractor)

    assert runnable.call_count == 2


def test_模型结构化结果不写入日志(
    caplog,
    production_contract,
    invoke_extract,
):
    sensitive_marker = "model-sensitive-output-marker"
    runnable = FakeStructuredRunnable(
        response={"unexpected": sensitive_marker}
    )
    extractor = production_contract.LangChainConstraintExtractor(
        FakeChatModel(runnable=runnable)
    )

    with pytest.raises(
        production_contract.DialogueConstraintExtractionError
    ):
        invoke_extract(copy.deepcopy(VALID_DIALOGUE), extractor)

    assert sensitive_marker not in caplog.text
