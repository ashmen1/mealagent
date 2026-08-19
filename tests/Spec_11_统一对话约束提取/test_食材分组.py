from __future__ import annotations

import copy

import pytest

from .spec11_support import (
    build_dish,
    build_ingredient_group,
    build_requirement,
    build_turn_result,
)


def _ingredient_evidence(
    dish_index: int,
    group_index: int,
    relation: str,
    values: list[str],
) -> dict[str, str]:
    prefix = (
        f"dishes[{dish_index}].required_ingredient_groups[{group_index}]"
    )
    evidence = {f"{prefix}.match": relation}
    evidence.update(
        {
            f"{prefix}.items[{index}].value": value
            for index, value in enumerate(values)
        }
    )
    return evidence


def test_任意食材合取生成一个all组(start_session):
    service, llm_client, session_id = start_session()
    group = build_ingredient_group(
        "all",
        build_requirement("番茄"),
        build_requirement("鸡蛋"),
        build_requirement("土豆"),
    )
    llm_client.response = build_turn_result(
        session_id,
        dishes=[build_dish(required_ingredient_groups=[group])],
        evidence=_ingredient_evidence(
            0, 0, "番茄、鸡蛋和土豆", ["番茄", "鸡蛋", "土豆"]
        ),
    )

    result = service.submit_turn(session_id, "想吃番茄、鸡蛋和土豆")

    assert result["merged_constraints"]["dishes"][0][
        "required_ingredient_groups"
    ] == [group]


def test_任意食材析取生成一个any组(start_session):
    service, llm_client, session_id = start_session()
    group = build_ingredient_group(
        "any",
        build_requirement("鱼"),
        build_requirement("鸡翅"),
    )
    llm_client.response = build_turn_result(
        session_id,
        dishes=[build_dish(required_ingredient_groups=[group])],
        evidence=_ingredient_evidence(
            0, 0, "鱼或者鸡翅", ["鱼", "鸡翅"]
        ),
    )

    result = service.submit_turn(session_id, "主菜考虑鱼或者鸡翅")

    assert result["merged_constraints"]["dishes"][0][
        "required_ingredient_groups"
    ] == [group]


def test_组间AND支持单项all加多项any(start_session):
    service, llm_client, session_id = start_session()
    groups = [
        build_ingredient_group("all", build_requirement("番茄")),
        build_ingredient_group(
            "any",
            build_requirement("鱼"),
            build_requirement("鸡翅"),
        ),
    ]
    evidence = {
        **_ingredient_evidence(0, 0, "番茄", ["番茄"]),
        **_ingredient_evidence(0, 1, "鱼或鸡翅", ["鱼", "鸡翅"]),
    }
    llm_client.response = build_turn_result(
        session_id,
        dishes=[build_dish(required_ingredient_groups=groups)],
        evidence=evidence,
    )

    result = service.submit_turn(
        session_id,
        "要番茄，并且鱼或鸡翅选一个",
    )

    assert result["merged_constraints"]["dishes"][0][
        "required_ingredient_groups"
    ] == groups


def test_现有食材只进入available_ingredients(start_session):
    service, llm_client, session_id = start_session()
    llm_client.response = build_turn_result(
        session_id,
        available_ingredients=["番茄", "鸡蛋", "土豆"],
        evidence={
            "available_ingredients[0]": "番茄",
            "available_ingredients[1]": "鸡蛋",
            "available_ingredients[2]": "土豆",
        },
    )

    result = service.submit_turn(
        session_id,
        "家里只剩番茄、鸡蛋和土豆",
    )

    merged = result["merged_constraints"]
    assert merged["available_ingredients"] == ["番茄", "鸡蛋", "土豆"]
    assert merged["dishes"][0]["required_ingredient_groups"] == []


@pytest.mark.parametrize(
    ("groups", "message", "evidence"),
    [
        (
            [{"match": "all", "items": []}],
            "必须有食材",
            {},
        ),
        (
            [
                build_ingredient_group(
                    "any",
                    build_requirement("鱼"),
                )
            ],
            "鱼或者别的",
            _ingredient_evidence(0, 0, "鱼或者别的", ["鱼"]),
        ),
        (
            [
                build_ingredient_group(
                    "some",
                    build_requirement("鱼"),
                    build_requirement("鸡翅"),
                )
            ],
            "鱼或者鸡翅",
            _ingredient_evidence(0, 0, "鱼或者鸡翅", ["鱼", "鸡翅"]),
        ),
        (
            [
                build_ingredient_group(
                    "all",
                    build_requirement("鱼"),
                    build_requirement("鱼"),
                )
            ],
            "鱼和鱼",
            _ingredient_evidence(0, 0, "鱼和鱼", ["鱼", "鱼"]),
        ),
        (
            [
                build_ingredient_group("all", build_requirement("鱼")),
                build_ingredient_group("all", build_requirement("鱼")),
            ],
            "鱼还要鱼",
            {
                **_ingredient_evidence(0, 0, "鱼", ["鱼"]),
                **_ingredient_evidence(0, 1, "鱼", ["鱼"]),
            },
        ),
    ],
    ids=[
        "all空项",
        "any只有一项",
        "未知match",
        "同组重复",
        "跨组重复",
    ],
)
def test_食材组非法结构返回502(
    groups,
    message,
    evidence,
    start_session,
    assert_dialogue_error,
):
    service, llm_client, session_id = start_session()
    invalid = build_turn_result(
        session_id,
        dishes=[build_dish(required_ingredient_groups=groups)],
        evidence=evidence,
    )
    llm_client.responses = [invalid, copy.deepcopy(invalid)]

    assert_dialogue_error(
        lambda: service.submit_turn(session_id, message),
        502,
    )


@pytest.mark.parametrize(
    ("requirement", "message"),
    [
        (build_requirement("不存在的食材"), "要不存在的食材"),
        (build_requirement("鱼", kind="category"), "要水产类"),
        (build_requirement("米", kind="concept"), "想吃米"),
        (build_requirement("鱼", kind="unknown"), "想吃鱼"),
    ],
)
def test_食材kind与value非法返回502(
    requirement,
    message,
    start_session,
    assert_dialogue_error,
):
    service, llm_client, session_id = start_session()
    group = build_ingredient_group("all", requirement)
    invalid = build_turn_result(
        session_id,
        dishes=[build_dish(required_ingredient_groups=[group])],
        evidence=_ingredient_evidence(
            0, 0, message, [requirement["value"]]
        ),
    )
    llm_client.responses = [invalid, copy.deepcopy(invalid)]

    assert_dialogue_error(
        lambda: service.submit_turn(session_id, message),
        502,
    )


def test_食材关系和每项值都必须有证据(
    start_session,
    assert_dialogue_error,
):
    service, llm_client, session_id = start_session()
    group = build_ingredient_group(
        "any",
        build_requirement("鱼"),
        build_requirement("鸡翅"),
    )
    invalid = build_turn_result(
        session_id,
        dishes=[build_dish(required_ingredient_groups=[group])],
        evidence={
            "dishes[0].required_ingredient_groups[0].items[0].value": "鱼",
            "dishes[0].required_ingredient_groups[0].items[1].value": "鸡翅",
        },
    )
    llm_client.responses = [invalid, copy.deepcopy(invalid)]

    assert_dialogue_error(
        lambda: service.submit_turn(session_id, "鱼或者鸡翅"),
        502,
    )
