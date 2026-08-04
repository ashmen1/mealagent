from __future__ import annotations

"""Spec_01 边界与冲突测试。"""

import pytest

from spec01_support import (
    assert_validation_error,
    invoke_extract,
    production_contract,
    profile_factory,
)


@pytest.mark.parametrize("special_populations", [["无"], []])
def test_无特殊人群时输出空数组(
    special_populations,
    profile_factory,
    invoke_extract,
):
    profile = profile_factory(special_populations=special_populations)

    result = invoke_extract(profile)

    assert result["special_populations"] == []


@pytest.mark.parametrize("allergens", [["无"], []])
def test_无过敏食材时输出空数组(allergens, profile_factory, invoke_extract):
    profile = profile_factory(allergens=allergens)

    result = invoke_extract(profile)

    assert result["allergens"] == []


@pytest.mark.parametrize("taste_preference", ["忽略", "无", ""])
def test_无口味要求时输出空对象(
    taste_preference,
    profile_factory,
    invoke_extract,
):
    profile = profile_factory(taste_preference=taste_preference)

    result = invoke_extract(profile)

    assert result["taste_preferences"] == {}


@pytest.mark.parametrize(
    ("taste_preference", "expected"),
    [
        ("甜", {"is_sweet": True}),
        ("不甜", {"is_sweet": False}),
    ],
)
def test_甜味肯定与否定转换为布尔值(
    taste_preference,
    expected,
    profile_factory,
    invoke_extract,
):
    profile = profile_factory(taste_preference=taste_preference)

    result = invoke_extract(profile)

    assert result["taste_preferences"] == expected


@pytest.mark.parametrize(
    ("taste_preference", "expected"),
    [
        ("咸", {"is_salty": True}),
        ("不咸", {"is_salty": False}),
    ],
)
def test_咸味肯定与否定转换为布尔值(
    taste_preference,
    expected,
    profile_factory,
    invoke_extract,
):
    profile = profile_factory(taste_preference=taste_preference)

    result = invoke_extract(profile)

    assert result["taste_preferences"] == expected


def test_复合口味拆成多个布尔字段(profile_factory, invoke_extract):
    profile = profile_factory(taste_preference="酸甜")

    result = invoke_extract(profile)

    assert result["taste_preferences"] == {
        "is_sour": True,
        "is_sweet": True,
    }


@pytest.mark.parametrize(
    ("taste_preference", "expected"),
    [
        ("甜", {"is_sweet": True}),
        ("清淡", {"is_light": True}),
        ("辣", {"is_spicy": True}),
        ("咸", {"is_salty": True}),
        ("酸", {"is_sour": True}),
        (
            "甜清淡辣咸酸",
            {
                "is_sweet": True,
                "is_light": True,
                "is_spicy": True,
                "is_salty": True,
                "is_sour": True,
            },
        ),
    ],
)
def test_口味只生成五种约定字段(
    taste_preference,
    expected,
    profile_factory,
    invoke_extract,
):
    profile = profile_factory(taste_preference=taste_preference)

    result = invoke_extract(profile)

    assert result["taste_preferences"] == expected
    assert set(result["taste_preferences"]) <= {
        "is_sweet",
        "is_light",
        "is_spicy",
        "is_salty",
        "is_sour",
    }


@pytest.mark.parametrize(
    ("overrides", "result_field", "expected"),
    [
        (
            {"special_populations": ["孕妇", "孕妇"]},
            "special_populations",
            ["孕妇"],
        ),
        ({"allergens": ["花生", "花生"]}, "allergens", ["花生"]),
        ({"taste_preference": "酸酸"}, "taste_preferences", {"is_sour": True}),
    ],
)
def test_重复值只保留一次(
    overrides,
    result_field,
    expected,
    profile_factory,
    invoke_extract,
):
    profile = profile_factory(**overrides)

    result = invoke_extract(profile)

    assert result[result_field] == expected


@pytest.mark.parametrize(
    "overrides",
    [
        {"special_populations": ["无", "孕妇"]},
        {"allergens": ["无", "花生"]},
        {"taste_preference": "无、甜"},
    ],
)
def test_无与其他值同时出现时返回400(
    overrides,
    profile_factory,
    assert_validation_error,
):
    profile = profile_factory(**overrides)

    assert_validation_error(profile)


@pytest.mark.parametrize("taste_preference", ["甜、不甜", "咸、不咸"])
def test_同一口味同时肯定和否定时返回400(
    taste_preference,
    profile_factory,
    assert_validation_error,
):
    profile = profile_factory(taste_preference=taste_preference)

    assert_validation_error(profile)


@pytest.mark.parametrize(
    "overrides",
    [
        {"special_populations": ["未配置特殊人群"]},
        {"taste_preference": "鲜"},
        {"allergens": ["未配置过敏食材"]},
    ],
)
def test_出现未配置值时返回400(
    overrides,
    profile_factory,
    assert_validation_error,
):
    profile = profile_factory(**overrides)

    assert_validation_error(profile)
