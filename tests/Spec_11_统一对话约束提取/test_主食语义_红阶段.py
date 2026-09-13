from __future__ import annotations

import copy

import pytest

from .spec11_support import (
    build_dish,
    build_dish_action,
    build_ingredient_group,
    build_requirement,
    build_turn_result,
)


def _staple_evidence(
    required: dict | None = None,
    excluded: list[str] | None = None,
) -> dict[str, str]:
    evidence: dict[str, str] = {"dishes[0].dish_type": "主食"}
    if required is not None:
        evidence["dishes[0].required_staple_ingredients.match"] = "主食来源"
        for index, item in enumerate(required["items"]):
            evidence[
                f"dishes[0].required_staple_ingredients.items[{index}]"
            ] = item
    for index, item in enumerate(excluded or []):
        evidence[f"dishes[0].excluded_staple_ingredients[{index}]"] = item
    return evidence


def test_输出Schema要求两个主食字段(production_contract) -> None:
    dish_schema = production_contract.output_schema["properties"]["dishes"][
        "items"
    ]

    assert "required_staple_ingredients" in dish_schema["required"]
    assert "excluded_staple_ingredients" in dish_schema["required"]
    required_schema = dish_schema["properties"]["required_staple_ingredients"]
    excluded_schema = dish_schema["properties"]["excluded_staple_ingredients"]
    assert required_schema["anyOf"]
    assert excluded_schema["type"] == "array"


def test_submit_turn正常提取换主食与排除主食(start_session) -> None:
    service, llm_client, session_id = start_session()
    required = {"match": "any", "items": ["玉米", "红薯"]}
    excluded = ["米饭"]
    llm_client.response = build_turn_result(
        session_id,
        dishes=[
            build_dish(
                dish_type="主食",
                required_staple_ingredients=required,
                excluded_staple_ingredients=excluded,
            )
        ],
        evidence=_staple_evidence(required, excluded),
    )

    result = service.submit_turn(
        session_id,
        "主食来源不要米饭，换成玉米或者红薯",
    )

    dish = result["merged_constraints"]["dishes"][0]
    assert dish["dish_type"] == "主食"
    assert dish["required_staple_ingredients"] == required
    assert dish["excluded_staple_ingredients"] == excluded
    assert dish["required_ingredient_groups"] == []


def test_想吃带玉米仍生成普通包含组(start_session) -> None:
    service, llm_client, session_id = start_session()
    group = build_ingredient_group("all", build_requirement("玉米"))
    llm_client.response = build_turn_result(
        session_id,
        dishes=[build_dish(required_ingredient_groups=[group])],
        evidence={
            "dishes[0].required_ingredient_groups[0].match": "带玉米",
            "dishes[0].required_ingredient_groups[0].items[0].value": "玉米",
        },
    )

    result = service.submit_turn(session_id, "想吃带玉米的食物")

    dish = result["merged_constraints"]["dishes"][0]
    assert dish["required_ingredient_groups"] == [group]
    assert dish["required_staple_ingredients"] is None
    assert dish["excluded_staple_ingredients"] == []


@pytest.mark.parametrize(
    "dish",
    [
        build_dish(
            required_staple_ingredients={
                "match": "all",
                "items": ["玉米"],
            }
        ),
        build_dish(excluded_staple_ingredients=["米饭"]),
    ],
    ids=["正向约束", "排除约束"],
)
def test_非主食Dish携带主食约束返回502(
    dish,
    start_session,
    assert_dialogue_error,
) -> None:
    service, llm_client, session_id = start_session()
    invalid = build_turn_result(
        session_id,
        dishes=[dish],
        evidence=_staple_evidence(
            dish["required_staple_ingredients"],
            dish["excluded_staple_ingredients"],
        ),
    )
    llm_client.responses = [invalid, copy.deepcopy(invalid)]

    error = assert_dialogue_error(
        lambda: service.submit_turn(session_id, "主食调整"),
        502,
    )
    assert "dish_type" in str(error) or "主食" in str(error)


@pytest.mark.parametrize(
    ("required", "expected_text"),
    [
        ({"match": "all", "items": []}, "至少"),
        ({"match": "any", "items": ["玉米"]}, "至少"),
        ({"match": "other", "items": ["玉米", "红薯"]}, "match"),
        ({"match": "all", "items": ["玉米", "玉米"]}, "重复"),
        ({"match": "all", "items": ["不存在的主食"]}, "不存在"),
    ],
    ids=["all空", "any单项", "未知match", "重复", "食材不存在"],
)
def test_主食来源结构非法返回502(
    required,
    expected_text,
    start_session,
    assert_dialogue_error,
) -> None:
    service, llm_client, session_id = start_session()
    invalid = build_turn_result(
        session_id,
        dishes=[
            build_dish(
                dish_type="主食",
                required_staple_ingredients=required,
            )
        ],
        evidence=_staple_evidence(required),
    )
    llm_client.responses = [invalid, copy.deepcopy(invalid)]

    error = assert_dialogue_error(
        lambda: service.submit_turn(session_id, "调整主食"),
        502,
    )
    assert expected_text in str(error)


@pytest.mark.parametrize(
    ("required", "excluded"),
    [
        ({"match": "all", "items": ["玉米"]}, ["玉米"]),
        ({"match": "all", "items": ["米饭"]}, ["大米"]),
        ({"match": "all", "items": ["大米"]}, ["米饭"]),
    ],
    ids=["精确重叠", "米饭覆盖大米", "大米被米饭覆盖"],
)
def test_主食正负展开集合重叠返回502(
    required,
    excluded,
    start_session,
    assert_dialogue_error,
) -> None:
    service, llm_client, session_id = start_session()
    invalid = build_turn_result(
        session_id,
        dishes=[
            build_dish(
                dish_type="主食",
                required_staple_ingredients=required,
                excluded_staple_ingredients=excluded,
            )
        ],
        evidence=_staple_evidence(required, excluded),
    )
    llm_client.responses = [invalid, copy.deepcopy(invalid)]

    error = assert_dialogue_error(
        lambda: service.submit_turn(session_id, "主食冲突"),
        502,
    )
    assert "重叠" in str(error)


def test_主食关系和值缺少任一证据返回502(
    start_session,
    assert_dialogue_error,
) -> None:
    service, llm_client, session_id = start_session()
    required = {"match": "any", "items": ["玉米", "红薯"]}
    invalid = build_turn_result(
        session_id,
        dishes=[
            build_dish(
                dish_type="主食",
                required_staple_ingredients=required,
            )
        ],
        evidence={
            "dishes[0].dish_type": "主食",
            "dishes[0].required_staple_ingredients.items[0]": "玉米",
            "dishes[0].required_staple_ingredients.items[1]": "红薯",
        },
    )
    llm_client.responses = [invalid, copy.deepcopy(invalid)]

    error = assert_dialogue_error(
        lambda: service.submit_turn(session_id, "主食换一下"),
        502,
    )
    assert "证据" in str(error)


def test_多轮换主食整体替换并仅清理旧单项all米饭组(
    start_session,
    db_session,
    production_contract,
) -> None:
    service, llm_client, session_id = start_session()
    old_singleton = build_ingredient_group("all", build_requirement("米饭"))
    composite = build_ingredient_group(
        "all",
        build_requirement("鱼"),
        build_requirement("米饭"),
    )
    any_group = build_ingredient_group(
        "any",
        build_requirement("玉米"),
        build_requirement("红薯"),
    )
    first_required = {"match": "all", "items": ["米饭"]}
    second_required = {"match": "any", "items": ["玉米", "红薯"]}
    first_evidence = {
        **_staple_evidence(first_required, ["面条"]),
        "dishes[0].required_ingredient_groups[0].match": "米饭",
        "dishes[0].required_ingredient_groups[0].items[0].value": "米饭",
        "dishes[0].required_ingredient_groups[1].match": "鱼和米饭",
        "dishes[0].required_ingredient_groups[1].items[0].value": "鱼",
        "dishes[0].required_ingredient_groups[1].items[1].value": "米饭",
        "dishes[0].required_ingredient_groups[2].match": "玉米或红薯",
        "dishes[0].required_ingredient_groups[2].items[0].value": "玉米",
        "dishes[0].required_ingredient_groups[2].items[1].value": "红薯",
    }
    old_state = build_turn_result(
        session_id,
        dishes=[
            build_dish(
                dish_type="主食",
                required_ingredient_groups=[
                    old_singleton,
                    composite,
                    any_group,
                ],
                required_staple_ingredients=first_required,
                excluded_staple_ingredients=["面条"],
            )
        ],
        evidence=first_evidence,
    )
    old_state.pop("change_actions")
    row = db_session.get(production_contract.DialogueSession, session_id)
    row.merged_constraints = old_state
    row.status = "ready_for_planning"
    db_session.commit()
    llm_client.responses = [
        build_turn_result(
            session_id,
            dishes=[
                build_dish(
                    dish_type="主食",
                    required_ingredient_groups=[composite, any_group],
                    required_staple_ingredients=second_required,
                    excluded_staple_ingredients=["面条", "米饭"],
                )
            ],
            evidence={
                "dishes[0].dish_type": "主食",
                "dishes[0].required_staple_ingredients.match": "主食来源",
                "dishes[0].required_staple_ingredients.items[0]": "玉米",
                "dishes[0].required_staple_ingredients.items[1]": "红薯",
                "dishes[0].excluded_staple_ingredients[1]": "米饭",
            },
            change_actions=[
                build_dish_action(
                    0,
                    "replace",
                    "主食不要米饭，主食来源换成玉米或者红薯",
                )
            ],
        ),
    ]

    result = service.submit_turn(
        session_id,
        "主食不要米饭，主食来源换成玉米或者红薯",
    )

    dish = result["merged_constraints"]["dishes"][0]
    assert dish["required_staple_ingredients"] == second_required
    assert dish["excluded_staple_ingredients"] == ["面条", "米饭"]
    assert dish["required_ingredient_groups"] == [composite, any_group]


def test_读取旧会话只补空主食字段且不猜普通食材(
    start_session,
    db_session,
    production_contract,
) -> None:
    service, _, session_id = start_session()
    ordinary = build_ingredient_group("all", build_requirement("米饭"))
    old_dish = build_dish(required_ingredient_groups=[ordinary])
    old_dish.pop("required_staple_ingredients")
    old_dish.pop("excluded_staple_ingredients")
    old_state = build_turn_result(session_id, dishes=[old_dish])
    old_state.pop("change_actions")
    row = db_session.get(production_contract.DialogueSession, session_id)
    row.merged_constraints = old_state
    row.status = "ready_for_planning"
    db_session.commit()

    result = service.get_session(session_id)

    dish = result["merged_constraints"]["dishes"][0]
    assert dish["required_staple_ingredients"] is None
    assert dish["excluded_staple_ingredients"] == []
    assert dish["required_ingredient_groups"] == [ordinary]
