from __future__ import annotations

import pytest

from .spec09_support import build_dish, build_get_state, build_merged, resolved


def _get_known_constraints(build_service, dish):
    merged = build_merged(meal_periods=["晚餐"], dishes=[dish])
    service, multi_turn, _ = build_service(resolved("晚餐"))
    multi_turn.get_result = build_get_state(merged)
    return service.get_session(101)["known_constraints"]


def test_get_session正常路径分开展示普通食材主食来源和排除主食(
    build_service,
) -> None:
    dish = build_dish(
        dish_type="主食",
        required_ingredient_groups=[
            {
                "match": "all",
                "items": [{"kind": "ingredient", "value": "玉米"}],
            }
        ],
        required_staple_ingredients={
            "match": "any",
            "items": ["玉米", "红薯"],
        },
        excluded_staple_ingredients=["米饭"],
    )

    items = _get_known_constraints(build_service, dish)
    selected = [
        (item["label"], item["value"])
        for item in items
        if "食材" in item["label"] or "主食" in item["label"]
    ]

    assert selected == [
        ("菜品组1所需食材", "玉米"),
        ("菜品组1主食来源", "玉米或红薯"),
        ("菜品组1排除主食", "米饭"),
    ]


@pytest.mark.parametrize(
    ("required", "expected"),
    [
        ({"match": "all", "items": ["玉米"]}, "玉米"),
        (
            {"match": "all", "items": ["玉米", "红薯", "燕麦"]},
            "玉米、红薯和燕麦",
        ),
        (
            {"match": "any", "items": ["玉米", "红薯", "燕麦"]},
            "玉米、红薯或燕麦",
        ),
    ],
    ids=["all单项", "all三项", "any三项"],
)
def test_主食来源按固定连接规则保序(build_service, required, expected) -> None:
    items = _get_known_constraints(
        build_service,
        build_dish(
            dish_type="主食",
            required_staple_ingredients=required,
        ),
    )

    main_source = next(
        item for item in items if item["label"] == "菜品组1主食来源"
    )
    assert main_source["value"] == expected


def test_多个排除主食使用顿号并保持输入顺序(build_service) -> None:
    items = _get_known_constraints(
        build_service,
        build_dish(
            dish_type="主食",
            excluded_staple_ingredients=["米饭", "面粉", "面条"],
        ),
    )

    excluded = next(
        item for item in items if item["label"] == "菜品组1排除主食"
    )
    assert excluded["value"] == "米饭、面粉、面条"


def test_空主食字段不产生展示行(build_service) -> None:
    items = _get_known_constraints(build_service, build_dish())

    assert all("主食来源" not in item["label"] for item in items)
    assert all("排除主食" not in item["label"] for item in items)
