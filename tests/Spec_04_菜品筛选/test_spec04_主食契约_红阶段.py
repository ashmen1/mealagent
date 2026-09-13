from __future__ import annotations

import pytest

from spec04_support import (
    assert_filter_error,
    build_integrated_constraints,
    build_integrated_dish,
    fake_driver,
    invoke_filter,
    production_contract,
)


@pytest.mark.parametrize(
    "field",
    ["required_staple_ingredients", "excluded_staple_ingredients"],
)
def test_主食字段缺失返回400(field, assert_filter_error, fake_driver) -> None:
    dish = build_integrated_dish()
    dish.pop(field)
    constraints = build_integrated_constraints(dishes=[dish])

    error = assert_filter_error(constraints, fake_driver, expected_status=400)
    assert field in str(error)


@pytest.mark.parametrize(
    "dish",
    [
        build_integrated_dish(
            required_staple_ingredients={
                "match": "all",
                "items": ["玉米"],
            }
        ),
        build_integrated_dish(excluded_staple_ingredients=["米饭"]),
    ],
    ids=["正向主食", "排除主食"],
)
def test_非主食Dish携带主食字段返回400(
    dish,
    assert_filter_error,
    fake_driver,
) -> None:
    error = assert_filter_error(
        build_integrated_constraints(dishes=[dish]),
        fake_driver,
        expected_status=400,
    )
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
def test_主食正负展开集合重叠返回400(
    required,
    excluded,
    assert_filter_error,
    fake_driver,
) -> None:
    dish = build_integrated_dish(
        dish_type="主食",
        required_staple_ingredients=required,
        excluded_staple_ingredients=excluded,
    )

    error = assert_filter_error(
        build_integrated_constraints(dishes=[dish]),
        fake_driver,
        expected_status=400,
    )
    assert "重叠" in str(error)


@pytest.mark.parametrize(
    ("required", "expected_text"),
    [
        ({"match": "all", "items": []}, "至少"),
        ({"match": "any", "items": ["玉米"]}, "至少"),
        ({"match": "some", "items": ["玉米", "红薯"]}, "match"),
        ({"match": "all", "items": ["玉米", "玉米"]}, "重复"),
        ({"match": "all", "items": [1]}, "字符串"),
    ],
    ids=["all空", "any单项", "未知match", "重复", "非字符串"],
)
def test_主食来源组非法返回400(
    required,
    expected_text,
    assert_filter_error,
    fake_driver,
) -> None:
    dish = build_integrated_dish(
        dish_type="主食",
        required_staple_ingredients=required,
    )

    error = assert_filter_error(
        build_integrated_constraints(dishes=[dish]),
        fake_driver,
        expected_status=400,
    )
    assert expected_text in str(error)


@pytest.mark.parametrize(
    ("excluded", "expected_text"),
    [(["米饭", "米饭"], "重复"), ([""], "非空"), ([1], "字符串")],
    ids=["重复", "空字符串", "非字符串"],
)
def test_排除主食数组非法返回400(
    excluded,
    expected_text,
    assert_filter_error,
    fake_driver,
) -> None:
    dish = build_integrated_dish(
        dish_type="主食",
        excluded_staple_ingredients=excluded,
    )

    error = assert_filter_error(
        build_integrated_constraints(dishes=[dish]),
        fake_driver,
        expected_status=400,
    )
    assert expected_text in str(error)


def test_filter正常路径生成主食角色参数化查询(
    invoke_filter,
    fake_driver,
) -> None:
    dish = build_integrated_dish(
        dish_type="主食",
        required_staple_ingredients={
            "match": "any",
            "items": ["玉米", "红薯"],
        },
        excluded_staple_ingredients=["米饭"],
    )

    invoke_filter(build_integrated_constraints(dishes=[dish]), fake_driver)

    query_text = "\n".join(query for query, _ in fake_driver.executed_queries)
    all_params = {
        key: value
        for _, params in fake_driver.executed_queries
        for key, value in params.items()
    }
    assert "is_staple_component" in query_text
    assert "玉米" in str(all_params.values())
    assert "红薯" in str(all_params.values())
    assert "大米" in str(all_params.values())
    assert "小米" not in str(all_params.values())
