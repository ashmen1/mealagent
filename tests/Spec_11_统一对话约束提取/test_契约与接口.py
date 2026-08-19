from __future__ import annotations

from copy import deepcopy


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
}


def test_LLM输出契约包含完整约束和变更声明(production_contract):
    schema = production_contract.output_schema

    assert set(schema["required"]) == MERGED_FIELDS | {"change_actions"}
    assert set(schema["properties"]) == MERGED_FIELDS | {"change_actions"}
    assert schema["additionalProperties"] is False


def test_可空数值字段以工具协议可识别的联合类型声明(production_contract):
    schema = production_contract.output_schema

    for field in (
        "diner_count",
        "total_dish_count",
        "max_total_time_minutes",
    ):
        assert schema["properties"][field] == {
            "type": ["integer", "null"],
            "minimum": 1,
        }
    dish_count = schema["properties"]["dishes"]["items"]["properties"][
        "count"
    ]
    assert dish_count == {
        "type": ["integer", "null"],
        "minimum": 1,
    }


def test_Dish契约只包含统一字段(production_contract):
    dish_schema = production_contract.output_schema["properties"]["dishes"][
        "items"
    ]

    assert set(dish_schema["required"]) == DISH_FIELDS
    assert set(dish_schema["properties"]) == DISH_FIELDS
    assert dish_schema["additionalProperties"] is False
    assert "required_ingredients" not in dish_schema["properties"]


def test_食材组契约明确all和any及最小项数(production_contract):
    dish_schema = production_contract.output_schema["properties"]["dishes"][
        "items"
    ]
    group_schema = dish_schema["properties"]["required_ingredient_groups"][
        "items"
    ]

    assert set(group_schema["required"]) == {"match", "items"}
    assert group_schema["properties"]["match"]["enum"] == ["all", "any"]
    assert group_schema["properties"]["items"]["minItems"] == 1
    assert group_schema["additionalProperties"] is False


def test_变更声明契约字段与允许动作固定(production_contract):
    action_schema = production_contract.output_schema["properties"][
        "change_actions"
    ]["items"]

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


def test_LangChain适配器绑定统一Schema(production_contract):
    captured: dict[str, object] = {}

    class StructuredModel:
        def invoke(self, prompt: str) -> dict[str, object]:
            return {"prompt": prompt}

    class ChatModel:
        def with_structured_output(self, schema, method):
            captured["schema"] = deepcopy(schema)
            captured["method"] = method
            return StructuredModel()

    extractor = production_contract.LangChainConstraintExtractor(ChatModel())

    captured_schema = captured["schema"]
    assert isinstance(captured_schema, dict)
    assert set(captured_schema["required"]) == MERGED_FIELDS | {
        "change_actions"
    }
    assert captured == {
        "schema": production_contract.output_schema,
        "method": "function_calling",
    }
    assert extractor("测试") == {"prompt": "测试"}


def test_LangChain适配器只归一化工具协议中的数值字段(production_contract):
    raw = {
        "dialogue_id": "12",
        "diner_count": "2",
        "total_dish_count": "4",
        "max_total_time_minutes": "30",
        "dishes": [{"count": "3"}, {"count": None}],
        "change_actions": [
            {"dish_index": "0", "evidence": "两个人"},
            {"dish_index": None, "evidence": "30"},
        ],
        "evidence": {"diner_count": "2"},
    }

    class StructuredModel:
        def invoke(self, prompt: str):
            del prompt
            return raw

    class ChatModel:
        def with_structured_output(self, schema, method):
            del schema, method
            return StructuredModel()

    extractor = production_contract.LangChainConstraintExtractor(ChatModel())

    assert extractor("测试") == {
        **raw,
        "dialogue_id": 12,
        "diner_count": 2,
        "total_dish_count": 4,
        "max_total_time_minutes": 30,
        "dishes": [{"count": 3}, {"count": None}],
        "change_actions": [
            {"dish_index": 0, "evidence": "两个人"},
            {"dish_index": None, "evidence": "30"},
        ],
    }
    assert raw["diner_count"] == "2"
    assert raw["evidence"] == {"diner_count": "2"}
