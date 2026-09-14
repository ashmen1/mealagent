from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from .spec11_support import build_turn_result


MERGED_FIELDS = {
    "dialogue_id",
    "meal_periods",
    "diner_count",
    "total_dish_count",
    "max_total_time_minutes",
    "max_difficulty",
    "available_ingredients",
    "dishes",
    "evidence",
}
DISH_FIELDS = {
    "count",
    "dish_type",
    "taste_preferences",
    "cuisines",
    "effects",
    "special_populations",
    "required_ingredient_groups",
    "required_staple_ingredients",
    "excluded_staple_ingredients",
}


def _resolve_schema(
    root_schema: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any]:
    reference = schema.get("$ref")
    if reference is None:
        return schema
    current: Any = root_schema
    for part in reference.removeprefix("#/").split("/"):
        current = current[part]
    assert isinstance(current, dict)
    return current


def _integer_schema(schema: dict[str, Any]) -> dict[str, Any]:
    branches = schema.get("anyOf", [schema])
    return next(branch for branch in branches if branch.get("type") == "integer")


def test_Pydantic输出契约包含完整约束和变更声明(production_contract):
    schema = production_contract.output_model.model_json_schema()

    assert schema == production_contract.output_schema
    assert set(schema["required"]) == MERGED_FIELDS | {"change_actions"}
    assert set(schema["properties"]) == MERGED_FIELDS | {"change_actions"}
    assert schema["additionalProperties"] is False


def test_可空数值字段保持正整数Schema(production_contract):
    schema = production_contract.output_schema

    for field in (
        "diner_count",
        "total_dish_count",
        "max_total_time_minutes",
    ):
        field_schema = schema["properties"][field]
        assert {branch.get("type") for branch in field_schema["anyOf"]} == {
            "integer",
            "null",
        }
        assert _integer_schema(field_schema)["minimum"] == 1

    dish_schema = _resolve_schema(
        schema,
        schema["properties"]["dishes"]["items"],
    )
    dish_count = dish_schema["properties"]["count"]
    assert _integer_schema(dish_count)["minimum"] == 1


def test_Dish契约只包含统一字段和受控枚举(production_contract):
    schema = production_contract.output_schema
    dish_schema = _resolve_schema(
        schema,
        schema["properties"]["dishes"]["items"],
    )

    assert set(dish_schema["required"]) == DISH_FIELDS
    assert set(dish_schema["properties"]) == DISH_FIELDS
    assert dish_schema["additionalProperties"] is False
    assert dish_schema["properties"]["dish_type"]["enum"] == [
        "菜",
        "汤",
        "主食",
        "小菜",
        "未指定",
    ]
    assert "required_ingredients" not in dish_schema["properties"]


def test_食材组契约明确all和any及最小项数(production_contract):
    schema = production_contract.output_schema
    dish_schema = _resolve_schema(
        schema,
        schema["properties"]["dishes"]["items"],
    )
    group_schema = _resolve_schema(
        schema,
        dish_schema["properties"]["required_ingredient_groups"]["items"],
    )

    assert set(group_schema["required"]) == {"match", "items"}
    assert group_schema["properties"]["match"]["enum"] == ["all", "any"]
    assert group_schema["properties"]["items"]["minItems"] == 1
    assert group_schema["properties"]["items"]["uniqueItems"] is True
    assert group_schema["additionalProperties"] is False


def test_变更声明契约字段与允许动作固定(production_contract):
    schema = production_contract.output_schema
    action_schema = _resolve_schema(
        schema,
        schema["properties"]["change_actions"]["items"],
    )

    assert set(action_schema["required"]) == {
        "field",
        "dish_index",
        "action",
        "evidence",
    }
    assert action_schema["properties"]["action"]["enum"] == [
        "add",
        "replace",
        "remove",
    ]
    assert action_schema["additionalProperties"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dialogue_id", True),
        ("diner_count", 2.0),
        ("total_dish_count", "2.0"),
        ("max_total_time_minutes", "两分钟"),
    ],
)
def test_Pydantic数字仅接受整数或十进制整数字符串(
    production_contract,
    field,
    value,
):
    raw = build_turn_result(12)
    raw[field] = value

    with pytest.raises(ValidationError):
        production_contract.output_model.model_validate(raw)


def test_Pydantic拒绝额外字段和非法枚举(production_contract):
    extra = build_turn_result(12, unsupported="value")
    invalid_enum = build_turn_result(12, meal_periods=["夜宵"])

    with pytest.raises(ValidationError):
        production_contract.output_model.model_validate(extra)
    with pytest.raises(ValidationError):
        production_contract.output_model.model_validate(invalid_enum)


def test_公开服务只提供统一会话接口(production_contract):
    service_type = production_contract.DialogueConstraintService

    assert callable(getattr(service_type, "create_session", None))
    assert callable(getattr(service_type, "submit_turn", None))
    assert callable(getattr(service_type, "get_session", None))
    assert not hasattr(service_type, "extract")
    assert not hasattr(
        production_contract.services_module,
        "MultiTurnConstraintService",
    )


def test_LLM基础设施只导出统一提取器(production_contract):
    assert hasattr(
        production_contract.llm_module,
        "LangChainConstraintExtractor",
    )
    assert not hasattr(
        production_contract.llm_module,
        "LangChainMultiTurnExtractor",
    )
    assert not hasattr(
        production_contract.llm_module,
        "create_langchain_multi_turn_extractor_from_environment",
    )


def test_LangChain适配器绑定Pydantic模型并返回完整字典(
    production_contract,
):
    captured: dict[str, object] = {}
    raw = build_turn_result(12)
    parsed = production_contract.output_model.model_validate(raw)

    class StructuredModel:
        def invoke(self, prompt: list[dict[str, str]]):
            captured["prompt"] = prompt
            return parsed

    class ChatModel:
        def with_structured_output(self, schema, method):
            captured["schema"] = schema
            captured["method"] = method
            return StructuredModel()

    extractor = production_contract.LangChainConstraintExtractor(ChatModel())
    prompt = [{"role": "human", "content": "测试"}]

    assert extractor(prompt) == raw
    assert captured == {
        "schema": production_contract.output_model,
        "method": "function_calling",
        "prompt": prompt,
    }


def test_LangChain适配器由Pydantic归一化工具整数参数(production_contract):
    raw = build_turn_result(
        12,
        diner_count="2",
        total_dish_count="4",
        max_total_time_minutes="30",
    )
    raw["dialogue_id"] = "12"
    raw["dishes"][0]["count"] = "3"
    raw["change_actions"] = [
        {
            "field": None,
            "dish_index": "0",
            "action": "replace",
            "evidence": "三道菜",
        }
    ]
    parsed = production_contract.output_model.model_validate(raw)

    class StructuredModel:
        def invoke(self, prompt):
            del prompt
            return parsed

    class ChatModel:
        def with_structured_output(self, schema, method):
            del schema, method
            return StructuredModel()

    extractor = production_contract.LangChainConstraintExtractor(ChatModel())
    result = extractor([{"role": "human", "content": "测试"}])

    assert result["dialogue_id"] == 12
    assert result["diner_count"] == 2
    assert result["total_dish_count"] == 4
    assert result["max_total_time_minutes"] == 30
    assert result["dishes"][0]["count"] == 3
    assert result["change_actions"][0]["dish_index"] == 0


def test_LangChain适配器将Pydantic校验失败映射为502(
    production_contract,
    assert_dialogue_error,
):
    invalid = build_turn_result(12, meal_periods=["夜宵"])

    class StructuredModel:
        def invoke(self, prompt):
            del prompt
            return production_contract.output_model.model_validate(invalid)

    class ChatModel:
        def with_structured_output(self, schema, method):
            del schema, method
            return StructuredModel()

    extractor = production_contract.LangChainConstraintExtractor(ChatModel())

    error = assert_dialogue_error(
        lambda: extractor([{"role": "human", "content": "测试"}]),
        502,
    )
    assert "meal_periods" in str(error)
