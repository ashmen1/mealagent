from __future__ import annotations

"""Spec_01 字段约束测试。"""

import pytest

from spec01_support import (
    assert_validation_error,
    invoke_extract,
    production_contract,
    profile_factory,
)


@pytest.mark.parametrize(
    "field_name",
    ["id", "special_populations", "taste_preference", "allergens"],
)
def test_必填字段缺失时返回400(
    field_name,
    profile_factory,
    assert_validation_error,
):
    profile = profile_factory()
    setattr(profile, field_name, None)

    assert_validation_error(profile)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("id", "1"),
        ("special_populations", "孕妇"),
        ("taste_preference", ["甜"]),
        ("allergens", "花生"),
    ],
)
def test_字段类型错误时返回400(
    field_name,
    invalid_value,
    profile_factory,
    assert_validation_error,
):
    profile = profile_factory(**{field_name: invalid_value})

    assert_validation_error(profile)


@pytest.mark.parametrize("profile_id", [1, 50])
def test_用户ID有效边界可正常提取(profile_id, profile_factory, invoke_extract):
    profile = profile_factory(id=profile_id)

    result = invoke_extract(profile)

    assert result == {
        "profile_id": profile_id,
        "special_populations": [],
        "taste_preferences": {"is_light": True},
        "allergens": [],
    }


@pytest.mark.parametrize("profile_id", [0, 51])
def test_用户ID超出范围时返回400(
    profile_id,
    profile_factory,
    assert_validation_error,
):
    profile = profile_factory(id=profile_id)

    assert_validation_error(profile)
