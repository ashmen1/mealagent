from __future__ import annotations

import pytest

from spec03_support import (
    assert_integration_error,
    build_dialogue_constraints,
    build_dish,
    build_profile_constraints,
    invoke_integrate,
    production_contract,
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


def test_integrate正常路径原样透传主食来源与排除项(invoke_integrate) -> None:
    required = {"match": "any", "items": ["玉米", "红薯"]}
    excluded = ["米饭"]
    dialogue = build_dialogue_constraints(
        dishes=[
            build_dish(
                dish_type="主食",
                required_staple_ingredients=required,
                excluded_staple_ingredients=excluded,
            )
        ],
        evidence=_staple_evidence(required, excluded),
    )

    result = invoke_integrate(build_profile_constraints(), dialogue)

    dish = result["dishes"][0]
    assert dish["required_staple_ingredients"] == required
    assert dish["excluded_staple_ingredients"] == excluded


def test_integrate正常路径显式保留空主食字段(invoke_integrate) -> None:
    result = invoke_integrate(
        build_profile_constraints(),
        build_dialogue_constraints(),
    )

    assert result["dishes"][0]["required_staple_ingredients"] is None
    assert result["dishes"][0]["excluded_staple_ingredients"] == []


@pytest.mark.parametrize(
    "dish",
    [
        build_dish(
            required_staple_ingredients={"match": "all", "items": ["玉米"]}
        ),
        build_dish(excluded_staple_ingredients=["米饭"]),
    ],
    ids=["正向约束", "排除约束"],
)
def test_非主食Dish携带主食约束返回400(
    dish,
    assert_integration_error,
) -> None:
    dialogue = build_dialogue_constraints(
        dishes=[dish],
        evidence=_staple_evidence(
            dish["required_staple_ingredients"],
            dish["excluded_staple_ingredients"],
        ),
    )

    error = assert_integration_error(build_profile_constraints(), dialogue)
    assert "dish_type" in str(error) or "主食" in str(error)


@pytest.mark.parametrize(
    ("required", "excluded"),
    [
        ({"match": "all", "items": ["玉米"]}, ["玉米"]),
        ({"match": "all", "items": ["米饭"]}, ["大米"]),
        ({"match": "all", "items": ["大米"]}, ["米饭"]),
    ],
    ids=["精确重叠", "米饭覆盖大米", "大米被米饭覆盖"],
)
def test_主食正负集合重叠返回400(
    required,
    excluded,
    assert_integration_error,
) -> None:
    dialogue = build_dialogue_constraints(
        dishes=[
            build_dish(
                dish_type="主食",
                required_staple_ingredients=required,
                excluded_staple_ingredients=excluded,
            )
        ],
        evidence=_staple_evidence(required, excluded),
    )

    error = assert_integration_error(build_profile_constraints(), dialogue)
    assert "重叠" in str(error)


@pytest.mark.parametrize(
    ("required", "expected_text"),
    [
        ({"match": "all", "items": []}, "至少"),
        ({"match": "any", "items": ["玉米"]}, "至少"),
        ({"match": "some", "items": ["玉米", "红薯"]}, "match"),
        ({"match": "all", "items": ["玉米", "玉米"]}, "重复"),
        ({"match": "all", "items": [""]}, "非空"),
    ],
    ids=["all空", "any单项", "未知match", "重复项", "空食材名"],
)
def test_主食来源组非法返回400(
    required,
    expected_text,
    assert_integration_error,
) -> None:
    dialogue = build_dialogue_constraints(
        dishes=[
            build_dish(
                dish_type="主食",
                required_staple_ingredients=required,
            )
        ],
        evidence=_staple_evidence(required),
    )

    error = assert_integration_error(build_profile_constraints(), dialogue)
    assert expected_text in str(error)


def test_主食来源all项与档案过敏同名时记录冲突(invoke_integrate) -> None:
    required = {"match": "all", "items": ["花生"]}
    dialogue = build_dialogue_constraints(
        dishes=[
            build_dish(
                dish_type="主食",
                required_staple_ingredients=required,
            )
        ],
        evidence=_staple_evidence(required),
    )

    result = invoke_integrate(
        build_profile_constraints(allergens=["花生"]),
        dialogue,
    )

    assert result["has_conflicts"] is True
    assert result["conflicts"][0]["required_ingredient"] == {
        "kind": "ingredient",
        "value": "花生",
    }
    assert result["conflicts"][0]["dialogue_path"] == (
        "dishes[0].required_staple_ingredients.items[0]"
    )


def test_主食来源any仍有安全项时不记录过敏冲突(invoke_integrate) -> None:
    required = {"match": "any", "items": ["花生", "红薯"]}
    dialogue = build_dialogue_constraints(
        dishes=[
            build_dish(
                dish_type="主食",
                required_staple_ingredients=required,
            )
        ],
        evidence=_staple_evidence(required),
    )

    result = invoke_integrate(
        build_profile_constraints(allergens=["花生"]),
        dialogue,
    )

    assert result["has_conflicts"] is False
    assert result["conflicts"] == []


def test_主食来源any全部过敏时逐项记录冲突(invoke_integrate) -> None:
    required = {"match": "any", "items": ["花生", "鸡蛋"]}
    dialogue = build_dialogue_constraints(
        dishes=[
            build_dish(
                dish_type="主食",
                required_staple_ingredients=required,
            )
        ],
        evidence=_staple_evidence(required),
    )

    result = invoke_integrate(
        build_profile_constraints(allergens=["花生", "鸡蛋"]),
        dialogue,
    )

    assert result["has_conflicts"] is True
    assert [item["required_ingredient"] for item in result["conflicts"]] == [
        {"kind": "ingredient", "value": "花生"},
        {"kind": "ingredient", "value": "鸡蛋"},
    ]
    assert [item["dialogue_path"] for item in result["conflicts"]] == [
        "dishes[0].required_staple_ingredients.items[0]",
        "dishes[0].required_staple_ingredients.items[1]",
    ]


def test_排除主食不并入过敏冲突(invoke_integrate) -> None:
    excluded = ["花生"]
    dialogue = build_dialogue_constraints(
        dishes=[
            build_dish(
                dish_type="主食",
                excluded_staple_ingredients=excluded,
            )
        ],
        evidence=_staple_evidence(excluded=excluded),
    )

    result = invoke_integrate(
        build_profile_constraints(allergens=["花生"]),
        dialogue,
    )

    assert result["has_conflicts"] is False
    assert result["allergens"] == ["花生"]
    assert result["dishes"][0]["excluded_staple_ingredients"] == ["花生"]
