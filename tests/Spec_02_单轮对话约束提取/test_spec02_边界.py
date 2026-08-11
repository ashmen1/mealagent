from __future__ import annotations

import copy
import importlib
from typing import Any

import pytest
from sqlalchemy.orm import Session

from backend.infrastructure.database.models import Ingredient

from spec02_support import (
    DISH_FIELDS,
    TOP_LEVEL_FIELDS,
    FakeLLMClient,
    assert_extraction_error,
    build_empty_dish,
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

GOLDEN_MESSAGES = [
    "今晚吃啥比较好？",
    "帮我想个简单点的早餐。",
    "中午想吃点清爽的，有没有那种适合夏天的搭配？",
    "晚上两个人吃，最近胃口不太好",
    "帮我想个带去公司的午饭吧",
    "我今天下班会比较晚，想做个半小时内能搞定的晚饭。",
    "家里现在就剩番茄、鸡蛋和土豆了，这顿饭还能怎么弄？要能当正餐。",
    "我今晚有点想吃面，再帮我配个别太抢味的小菜。",
    "周末想在家吃得有点仪式感，但我又不想做太复杂。",
    "晚上有点饿，想吃个热乎点的夜宵",
    "想做顿一家四口吃的晚饭",
    "想做个四菜一汤，营养均衡一点的",
    "今天状态不太好，想吃点暖胃的。",
    "想做个四菜一汤，营养均衡一点的，小孩不吃辣，老人牙口不好",
]

GOLDEN_PROMPT_EXPECTATIONS = [
    ("晚餐",),
    ("早餐",),
    ("午餐", "is_light", "true"),
    ("晚餐", "diner_count", "2", "养胃健胃消食"),
    ("午餐", "上班族"),
    ("晚餐", "max_total_time_minutes", "30", "上班族"),
    ("available_ingredients", "番茄", "鸡蛋", "土豆"),
    ("晚餐", "count", "主食", "面", "小菜", "is_light"),
    ("西餐风味",),
    ("晚餐",),
    ("晚餐", "diner_count", "4"),
    ("count", "4", "菜", "1", "汤"),
    ("养胃健胃消食",),
    ("count", "4", "菜", "1", "汤", "is_spicy", "false", "儿童", "老人"),
]


def create_client(result: dict[str, Any]) -> FakeLLMClient:
    return FakeLLMClient(result)


def assert_response_error(
    assert_extraction_error,
    dialogue: dict[str, Any],
    structured_response: object,
) -> FakeLLMClient:
    client = FakeLLMClient(structured_response)
    assert_extraction_error(dialogue, client, 502)
    assert client.call_count == 1
    return client


INVALID_DIALOGUES = [
    ({"turn_count": 1, "user_messages": ["晚饭吃什么"]}, "缺少id"),
    ({"id": 1, "user_messages": ["晚饭吃什么"]}, "缺少turn_count"),
    ({"id": 1, "turn_count": 1}, "缺少user_messages"),
    ({"id": 0, "turn_count": 1, "user_messages": ["晚饭吃什么"]}, "id为0"),
    ({"id": -1, "turn_count": 1, "user_messages": ["晚饭吃什么"]}, "id为负数"),
    ({"id": True, "turn_count": 1, "user_messages": ["晚饭吃什么"]}, "id为布尔值"),
    ({"id": "1", "turn_count": 1, "user_messages": ["晚饭吃什么"]}, "id类型错误"),
    ({"id": 1, "turn_count": 0, "user_messages": ["晚饭吃什么"]}, "turn_count为0"),
    ({"id": 1, "turn_count": 2, "user_messages": ["第一轮", "第二轮"]}, "多轮对话"),
    ({"id": 1, "turn_count": True, "user_messages": ["晚饭吃什么"]}, "turn_count为布尔值"),
    ({"id": 1, "turn_count": "1", "user_messages": ["晚饭吃什么"]}, "turn_count类型错误"),
    ({"id": 1, "turn_count": 1, "user_messages": []}, "消息数组为空"),
    ({"id": 1, "turn_count": 1, "user_messages": ["第一条", "第二条"]}, "消息数组多项"),
    ({"id": 1, "turn_count": 1, "user_messages": "晚饭吃什么"}, "消息不是数组"),
    ({"id": 1, "turn_count": 1, "user_messages": [1]}, "消息元素不是字符串"),
    ({"id": 1, "turn_count": 1, "user_messages": [""]}, "消息为空字符串"),
    ({"id": 1, "turn_count": 1, "user_messages": ["   "]}, "消息仅有空白"),
]


@pytest.mark.parametrize(
    ("dialogue", "case_name"),
    INVALID_DIALOGUES,
    ids=[case_name for _, case_name in INVALID_DIALOGUES],
)
def test_非法单轮输入返回400且不调用LLM(
    dialogue,
    case_name,
    assert_extraction_error,
):
    del case_name
    client = create_client(build_empty_result())

    assert_extraction_error(dialogue, client, 400, session=object())

    assert client.call_count == 0


def test_Prompt包含完整契约与现有用例(invoke_extract):
    dialogue = copy.deepcopy(VALID_DIALOGUE)
    client = create_client(build_empty_result(dialogue["id"]))

    invoke_extract(dialogue, client)

    assert client.call_count == 1
    prompt = client.prompts[0]
    for field in (*TOP_LEVEL_FIELDS, *DISH_FIELDS):
        assert field in prompt
    for field in ("kind", "value"):
        assert field in prompt
    assert any(value in prompt for value in ("integer", "整数"))
    assert any(value in prompt for value in ("string", "字符串"))
    assert any(value in prompt for value in ("boolean", "布尔"))
    assert "null" in prompt
    assert "[]" in prompt
    assert "{}" in prompt
    assert f"dialogue_id={dialogue['id']}" in prompt
    assert "不得复制示例id" in prompt

    allowed_values = (
        "下午茶",
        "晚餐",
        "早餐",
        "午餐",
        "菜",
        "汤",
        "主食",
        "小菜",
        "未指定",
        "is_sweet",
        "is_light",
        "is_spicy",
        "is_salty",
        "is_sour",
        "西餐风味",
        "东北菜",
        "粤菜",
        "川湘菜",
        "江浙菜",
        "助眠",
        "减脂",
        "养胃健胃消食",
        "贫血",
        "哺乳",
        "上班族",
        "儿童",
        "老人",
        "更年期",
        "ingredient",
        "category",
        "concept",
        "番茄",
        "粮食",
        "面",
    )
    for value in allowed_values:
        assert value in prompt

    normalization_values = (
        "微辣",
        "香辣",
        "麻辣",
        "不辣",
        "咸鲜",
        "暖胃",
        "胃口不好",
        "养胃",
        "健胃消食",
        "便秘",
        "夜宵",
        "公司",
        "上班",
        "下班",
        "仪式感",
        "清爽",
        "别太抢味",
    )
    for value in normalization_values:
        assert value in prompt
    assert "核心食材" in prompt
    assert "盐" in prompt
    assert "油" in prompt
    assert "水" in prompt
    assert "全部使用" in prompt
    assert "忽略" in prompt

    for index, message in enumerate(GOLDEN_MESSAGES):
        assert message in prompt
        block_start = prompt.index(message) + len(message)
        if index + 1 < len(GOLDEN_MESSAGES):
            block_end = prompt.index(GOLDEN_MESSAGES[index + 1], block_start)
        else:
            block_end = len(prompt)
        example_block = prompt[block_start:block_end]
        for expected_value in GOLDEN_PROMPT_EXPECTATIONS[index]:
            assert expected_value in example_block


INVALID_STRUCTURED_RESPONSES = [
    (None, "空响应"),
    ("{}", "普通JSON文本"),
    ("```json\n{}\n```", "Markdown代码块"),
    ("结果如下：{}", "解释文字"),
    ("[]", "数组"),
    ([], "数组对象"),
    (1, "数字标量"),
]


@pytest.mark.parametrize(
    ("structured_response", "case_name"),
    INVALID_STRUCTURED_RESPONSES,
    ids=[case_name for _, case_name in INVALID_STRUCTURED_RESPONSES],
)
def test_LangChain必须返回结构化对象且不降级解析文本(
    structured_response,
    case_name,
    assert_extraction_error,
):
    del case_name
    assert_response_error(
        assert_extraction_error,
        copy.deepcopy(VALID_DIALOGUE),
        structured_response,
    )


@pytest.mark.parametrize("field", TOP_LEVEL_FIELDS)
def test_LLM响应缺少顶层必填字段返回502(field, assert_extraction_error):
    result = build_empty_result()
    result.pop(field)

    assert_response_error(
        assert_extraction_error,
        copy.deepcopy(VALID_DIALOGUE),
        result,
    )


@pytest.mark.parametrize("field", DISH_FIELDS)
def test_LLM响应缺少Dish必填字段返回502(field, assert_extraction_error):
    result = build_empty_result()
    result["dishes"][0].pop(field)

    assert_response_error(
        assert_extraction_error,
        copy.deepcopy(VALID_DIALOGUE),
        result,
    )


TOP_LEVEL_TYPE_ERRORS = [
    ("dialogue_id", "1"),
    ("meal_periods", "晚餐"),
    ("diner_count", 1.5),
    ("max_total_time_minutes", True),
    ("available_ingredients", "番茄"),
    ("dishes", {}),
    ("evidence", []),
]


@pytest.mark.parametrize(("field", "bad_value"), TOP_LEVEL_TYPE_ERRORS)
def test_LLM响应顶层字段类型错误返回502(
    field,
    bad_value,
    assert_extraction_error,
):
    result = build_empty_result()
    result[field] = bad_value

    assert_response_error(
        assert_extraction_error,
        copy.deepcopy(VALID_DIALOGUE),
        result,
    )


DISH_TYPE_ERRORS = [
    ("count", 1.5),
    ("dish_type", 1),
    ("taste_preferences", []),
    ("cuisines", {}),
    ("effects", "助眠"),
    ("special_populations", [1]),
    ("required_ingredients", [1]),
]


@pytest.mark.parametrize(("field", "bad_value"), DISH_TYPE_ERRORS)
def test_LLM响应Dish字段类型错误返回502(
    field,
    bad_value,
    assert_extraction_error,
):
    result = build_empty_result()
    result["dishes"][0][field] = bad_value

    assert_response_error(
        assert_extraction_error,
        copy.deepcopy(VALID_DIALOGUE),
        result,
    )


def test_口味值必须为布尔值(assert_extraction_error):
    result = build_empty_result()
    result["dishes"][0]["taste_preferences"] = {"is_spicy": "false"}
    result["evidence"] = {
        "dishes[0].taste_preferences.is_spicy": "今晚"
    }

    assert_response_error(
        assert_extraction_error,
        copy.deepcopy(VALID_DIALOGUE),
        result,
    )


def test_顶层未声明字段返回502(assert_extraction_error):
    result = build_empty_result()
    result["menu_total"] = 1

    assert_response_error(
        assert_extraction_error,
        copy.deepcopy(VALID_DIALOGUE),
        result,
    )


def test_Dish未声明字段返回502(assert_extraction_error):
    result = build_empty_result()
    result["dishes"][0]["description"] = "随意"

    assert_response_error(
        assert_extraction_error,
        copy.deepcopy(VALID_DIALOGUE),
        result,
    )


def test_dialogue_id必须等于输入id(assert_extraction_error):
    result = build_empty_result(dialogue_id=2)

    assert_response_error(
        assert_extraction_error,
        copy.deepcopy(VALID_DIALOGUE),
        result,
    )


def test_dishes至少包含一项(assert_extraction_error):
    result = build_empty_result()
    result["dishes"] = []

    assert_response_error(
        assert_extraction_error,
        copy.deepcopy(VALID_DIALOGUE),
        result,
    )


def test_正整数最小值1有效(invoke_extract):
    message = "1个人最多1分钟做1道菜"
    dialogue = {"id": 1, "turn_count": 1, "user_messages": [message]}
    result = build_empty_result()
    result["diner_count"] = 1
    result["max_total_time_minutes"] = 1
    result["dishes"] = [build_empty_dish()]
    result["dishes"][0].update({"count": 1, "dish_type": "菜"})
    result["evidence"] = {
        "diner_count": "1个人",
        "max_total_time_minutes": "1分钟",
        "dishes[0].count": "1道菜",
        "dishes[0].dish_type": "菜",
    }
    client = create_client(result)

    assert invoke_extract(dialogue, client) == result
    assert client.call_count == 1


def test_未规定正整数上限(invoke_extract):
    maximum = 2_147_483_647
    message = f"{maximum}个人最多{maximum}分钟做{maximum}道菜"
    dialogue = {"id": maximum, "turn_count": 1, "user_messages": [message]}
    result = build_empty_result(maximum)
    result["diner_count"] = maximum
    result["max_total_time_minutes"] = maximum
    result["dishes"] = [build_empty_dish()]
    result["dishes"][0].update({"count": maximum, "dish_type": "菜"})
    result["evidence"] = {
        "diner_count": f"{maximum}个人",
        "max_total_time_minutes": f"{maximum}分钟",
        "dishes[0].count": f"{maximum}道菜",
        "dishes[0].dish_type": "菜",
    }
    client = create_client(result)

    assert invoke_extract(dialogue, client) == result
    assert client.call_count == 1


@pytest.mark.parametrize("field", ["diner_count", "max_total_time_minutes", "dish_count"])
@pytest.mark.parametrize("bad_value", [0, -1, True])
def test_数量字段拒绝非正整数(
    field,
    bad_value,
    assert_extraction_error,
):
    dialogue = {"id": 1, "turn_count": 1, "user_messages": ["人数时间数量"]}
    result = build_empty_result()
    if field == "dish_count":
        result["dishes"][0]["count"] = bad_value
        result["evidence"] = {"dishes[0].count": "数量"}
    else:
        result[field] = bad_value
        result["evidence"] = {field: "人数" if field == "diner_count" else "时间"}

    assert_response_error(
        assert_extraction_error,
        dialogue,
        result,
    )


INVALID_ALLOWED_VALUES = [
    ("meal_period", "夜宵"),
    ("dish_type", "甜品"),
    ("taste_key", "is_hot"),
    ("cuisine", "法餐"),
    ("effect", "降火"),
    ("population", "孕妇"),
]


@pytest.mark.parametrize(("kind", "bad_value"), INVALID_ALLOWED_VALUES)
def test_字段值不在允许范围返回502(
    kind,
    bad_value,
    assert_extraction_error,
):
    dialogue = {"id": 1, "turn_count": 1, "user_messages": [f"想要{bad_value}"]}
    result = build_empty_result()
    if kind == "meal_period":
        result["meal_periods"] = [bad_value]
        path = "meal_periods[0]"
    elif kind == "dish_type":
        result["dishes"][0]["dish_type"] = bad_value
        path = "dishes[0].dish_type"
    elif kind == "taste_key":
        result["dishes"][0]["taste_preferences"] = {bad_value: True}
        path = f"dishes[0].taste_preferences.{bad_value}"
    else:
        field_by_kind = {
            "cuisine": "cuisines",
            "effect": "effects",
            "population": "special_populations",
        }
        field = field_by_kind[kind]
        result["dishes"][0][field] = [bad_value]
        path = f"dishes[0].{field}[0]"
    result["evidence"] = {path: bad_value}

    assert_response_error(
        assert_extraction_error,
        dialogue,
        result,
    )


@pytest.mark.parametrize(
    "kind",
    [
        "meal_periods",
        "available_ingredients",
        "cuisines",
        "effects",
        "special_populations",
        "required_ingredients",
        "dishes",
    ],
)
def test_数组内不允许重复值(kind, assert_extraction_error):
    value_by_kind = {
        "meal_periods": "晚餐",
        "available_ingredients": "番茄",
        "cuisines": "粤菜",
        "effects": "助眠",
        "special_populations": "老人",
        "required_ingredients": "番茄",
    }
    value = value_by_kind.get(kind, "")
    dialogue = {"id": 1, "turn_count": 1, "user_messages": [f"想要{value}"]}
    result = build_empty_result()
    if kind == "dishes":
        result["dishes"] = [build_empty_dish(), build_empty_dish()]
    elif kind in ("meal_periods", "available_ingredients"):
        result[kind] = [value, value]
        result["evidence"] = {
            f"{kind}[0]": value,
            f"{kind}[1]": value,
        }
    else:
        if kind == "required_ingredients":
            requirement = {"kind": "ingredient", "value": value}
            result["dishes"][0][kind] = [requirement, requirement]
            result["evidence"] = {
                f"dishes[0].{kind}[0].value": value,
                f"dishes[0].{kind}[1].value": value,
            }
        else:
            result["dishes"][0][kind] = [value, value]
            result["evidence"] = {
                f"dishes[0].{kind}[0]": value,
                f"dishes[0].{kind}[1]": value,
            }

    assert_response_error(
        assert_extraction_error,
        dialogue,
        result,
    )


def test_同一口味同时肯定和否定返回502(assert_extraction_error):
    dialogue = {"id": 1, "turn_count": 1, "user_messages": ["不要辣"]}
    result = build_empty_result()
    result["dishes"][0]["taste_preferences"] = {
        "is_spicy": [True, False]
    }
    result["evidence"] = {
        "dishes[0].taste_preferences.is_spicy": "不要辣"
    }

    assert_response_error(
        assert_extraction_error,
        dialogue,
        result,
    )


@pytest.mark.parametrize(
    ("kind", "ingredient", "description"),
    [
        ("ingredient", "番茄", "标准食材"),
        ("category", "粮食", "食材类别"),
        ("concept", "面", "已配置概念"),
    ],
    ids=["标准食材", "食材类别", "已配置概念"],
)
def test_required_ingredients接受已配置食材值(
    kind,
    ingredient,
    description,
    invoke_extract,
):
    del description
    dialogue = {"id": 1, "turn_count": 1, "user_messages": [f"想吃{ingredient}"]}
    result = build_empty_result()
    result["dishes"][0]["required_ingredients"] = [
        {"kind": kind, "value": ingredient}
    ]
    result["evidence"] = {
        "dishes[0].required_ingredients[0].value": ingredient
    }
    client = create_client(result)

    assert invoke_extract(dialogue, client) == result
    assert client.call_count == 1


@pytest.mark.parametrize(
    "requirement",
    [
        {"kind": "unknown", "value": "番茄"},
        {"kind": "ingredient", "value": "粮食"},
        {"kind": "category", "value": "番茄"},
        {"kind": "concept", "value": "番茄"},
        {"value": "番茄"},
        {"kind": "ingredient"},
        {"kind": "ingredient", "value": "番茄", "extra": True},
    ],
    ids=[
        "非法kind",
        "类别伪装成标准食材",
        "标准食材伪装成类别",
        "标准食材伪装成概念",
        "缺少kind",
        "缺少value",
        "未声明字段",
    ],
)
def test_required_ingredients类型和值必须匹配(
    requirement,
    assert_extraction_error,
):
    dialogue = {"id": 1, "turn_count": 1, "user_messages": ["想吃番茄"]}
    result = build_empty_result()
    result["dishes"][0]["required_ingredients"] = [requirement]
    if isinstance(requirement.get("value"), str):
        result["evidence"] = {
            "dishes[0].required_ingredients[0].value": "番茄"
        }

    assert_response_error(
        assert_extraction_error,
        dialogue,
        result,
    )


@pytest.mark.parametrize("location", ["available", "required"])
def test_不存在的食材值返回502(location, assert_extraction_error):
    ingredient = "火星菜"
    dialogue = {"id": 1, "turn_count": 1, "user_messages": [f"想吃{ingredient}"]}
    result = build_empty_result()
    if location == "available":
        result["available_ingredients"] = [ingredient]
        path = "available_ingredients[0]"
    else:
        result["dishes"][0]["required_ingredients"] = [
            {"kind": "ingredient", "value": ingredient}
        ]
        path = "dishes[0].required_ingredients[0].value"
    result["evidence"] = {path: ingredient}

    assert_response_error(
        assert_extraction_error,
        dialogue,
        result,
    )


def test_面概念在提取结果中不提前展开(invoke_extract):
    dialogue = {"id": 1, "turn_count": 1, "user_messages": ["今晚想吃面"]}
    result = build_empty_result()
    result["meal_periods"] = ["晚餐"]
    result["dishes"][0]["required_ingredients"] = [
        {"kind": "concept", "value": "面"}
    ]
    result["evidence"] = {
        "meal_periods[0]": "今晚",
        "dishes[0].required_ingredients[0].value": "面",
    }
    client = create_client(result)

    assert invoke_extract(dialogue, client) == result
    assert result["dishes"][0]["required_ingredients"] == [
        {"kind": "concept", "value": "面"}
    ]


def test_可用食材只保留核心食材(invoke_extract):
    message = "家里只剩番茄、盐、油和水"
    dialogue = {"id": 1, "turn_count": 1, "user_messages": [message]}
    result = build_empty_result()
    result["available_ingredients"] = ["番茄"]
    result["evidence"] = {"available_ingredients[0]": "番茄"}
    client = create_client(result)

    assert invoke_extract(dialogue, client) == result
    assert result["available_ingredients"] == ["番茄"]


def test_每个非空约束都必须提供证据(assert_extraction_error):
    result = build_empty_result()
    result["meal_periods"] = ["晚餐"]

    assert_response_error(
        assert_extraction_error,
        copy.deepcopy(VALID_DIALOGUE),
        result,
    )


def test_evidence不接受无效字段路径(assert_extraction_error):
    result = build_empty_result()
    result["meal_periods"] = ["晚餐"]
    result["evidence"] = {
        "meal_periods[0]": "今晚",
        "unknown.path": "今晚",
    }

    assert_response_error(
        assert_extraction_error,
        copy.deepcopy(VALID_DIALOGUE),
        result,
    )


@pytest.mark.parametrize(
    "bad_evidence",
    ["晚上吃", "早餐", ""],
    ids=["非连续片段", "原文不存在", "空证据"],
)
def test_evidence必须是连续非空原文片段(
    bad_evidence,
    assert_extraction_error,
):
    dialogue = {"id": 1, "turn_count": 1, "user_messages": ["晚上两个人吃"]}
    result = build_empty_result()
    result["meal_periods"] = ["晚餐"]
    result["evidence"] = {"meal_periods[0]": bad_evidence}

    assert_response_error(
        assert_extraction_error,
        dialogue,
        result,
    )


def test_没有约束时不接受多余证据(assert_extraction_error):
    result = build_empty_result()
    result["evidence"] = {"meal_periods[0]": "今晚"}

    assert_response_error(
        assert_extraction_error,
        copy.deepcopy(VALID_DIALOGUE),
        result,
    )


@pytest.mark.parametrize(
    "service_error",
    [TimeoutError("请求超时"), ConnectionError("服务不可用")],
    ids=["请求超时", "服务不可用"],
)
def test_LLM服务异常返回503且不重试(
    service_error,
    assert_extraction_error,
):
    client = FakeLLMClient(error=service_error)

    assert_extraction_error(copy.deepcopy(VALID_DIALOGUE), client, 503)

    assert client.call_count == 1


def test_LLM客户端编程异常原样暴露(invoke_extract):
    programming_error = RuntimeError("客户端配置错误")
    client = FakeLLMClient(error=programming_error)

    with pytest.raises(RuntimeError) as captured:
        invoke_extract(copy.deepcopy(VALID_DIALOGUE), client)

    assert captured.value is programming_error
    assert client.call_count == 1


@pytest.mark.parametrize(
    "bad_session",
    [object(), Session()],
    ids=["错误Session类型", "未绑定Session"],
)
def test_数据库Session无效或查询失败返回500且不调用LLM(
    bad_session,
    assert_extraction_error,
):
    client = create_client(build_empty_result())

    assert_extraction_error(
        copy.deepcopy(VALID_DIALOGUE),
        client,
        500,
        session=bad_session,
    )

    assert client.call_count == 0


def test_Session工厂创建失败返回500且不调用LLM(production_contract):
    client = create_client(build_empty_result())

    def fail_to_create_session():
        raise RuntimeError("数据库不可达")

    service = production_contract.DialogueConstraintService(
        fail_to_create_session,
        client,
    )

    with pytest.raises(
        production_contract.DialogueConstraintExtractionError
    ) as captured:
        service.extract(copy.deepcopy(VALID_DIALOGUE))

    assert captured.value.status_code == 500
    assert client.call_count == 0


def test_一次查询加载食材名与类别(
    monkeypatch,
    ingredient_session,
    invoke_extract,
):
    execute_calls = 0
    original_execute = ingredient_session.execute

    def record_execute(*args, **kwargs):
        nonlocal execute_calls
        execute_calls += 1
        return original_execute(*args, **kwargs)

    monkeypatch.setattr(ingredient_session, "execute", record_execute)
    client = create_client(build_empty_result())

    invoke_extract(copy.deepcopy(VALID_DIALOGUE), client)

    assert execute_calls == 1


def test_Service关闭Session且不提交事务(
    ingredient_session,
    invoke_extract,
    monkeypatch,
):
    close_calls = 0
    original_close = ingredient_session.close

    def record_close():
        nonlocal close_calls
        close_calls += 1
        return original_close()

    monkeypatch.setattr(ingredient_session, "close", record_close)
    ingredient_session.add(
        Ingredient(id=5, name="待回滚食材", category="测试类别", aliases=[])
    )
    client = create_client(build_empty_result())

    invoke_extract(copy.deepcopy(VALID_DIALOGUE), client)
    ingredient_session.rollback()

    assert close_calls == 1
    assert ingredient_session.get(Ingredient, 5) is None
    assert ingredient_session.get(Ingredient, 1) is not None


def test_不支持的自由描述返回稳定空结构(invoke_extract):
    dialogue = {
        "id": 20,
        "turn_count": 1,
        "user_messages": ["想吃点简单、热乎、适合夏天而且牙口友好的。"],
    }
    expected = build_empty_result(dialogue_id=20)
    client = create_client(expected)

    assert invoke_extract(dialogue, client) == expected
    assert client.call_count == 1


def test_API_Key与用户原文不进入日志(
    monkeypatch,
    caplog,
    invoke_extract,
):
    secret_marker = "spec02-secret-must-not-leak"
    monkeypatch.setenv("LLM_API_KEY", secret_marker)
    client = create_client(build_empty_result())

    invoke_extract(copy.deepcopy(VALID_DIALOGUE), client)

    assert client.call_count == 1
    assert secret_marker not in client.prompts[0]
    assert secret_marker not in caplog.text
    assert VALID_DIALOGUE["user_messages"][0] not in caplog.text


def test_旧三参数函数不再对外公开():
    module = importlib.import_module("backend.services.dialogue_constraints")

    assert not hasattr(module, "extract_single_turn_constraints")
