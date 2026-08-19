from __future__ import annotations

import copy
from typing import Any

import pytest

from backend.services.constraint_integration import ConstraintIntegrationService
from backend.services.dish_filtering import DishFilteringService

from .conftest import (
    build_dish,
    build_group,
    build_integrated,
    build_integrated_dish,
    build_merged,
    build_profile,
    build_requirement,
)


class Record(dict[str, Any]):
    """支持按键读取的图查询记录。"""


class FakeSession:
    def __init__(self, driver: FakeDriver) -> None:
        self.driver = driver

    def __enter__(self) -> FakeSession:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def run(self, query: str, **params: Any) -> list[Record]:
        self.driver.calls.append((query, copy.deepcopy(params)))
        if "AS ingredient_name" in query:
            return [
                Record(ingredient_name=name)
                for name in params["ingredient_names"]
            ]
        return [
            Record(
                recipe_name="番茄炒蛋",
                recipe_type="菜",
                matched_tags=["午餐"],
            )
        ]


class FakeDriver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def session(self) -> FakeSession:
        return FakeSession(self)


def test_约束整合原样保留任意食材分组() -> None:
    groups = [
        build_group(build_requirement("番茄")),
        build_group(
            build_requirement("鱼"),
            build_requirement("鸡翅"),
            match="any",
        ),
    ]
    merged = build_merged(dishes=[build_dish(required_ingredient_groups=groups)])

    result = ConstraintIntegrationService().integrate(
        build_profile(),
        merged,
    )

    assert result["dishes"][0]["required_ingredient_groups"] == groups
    assert "required_ingredients" not in result["dishes"][0]


def test_all组任一食材与过敏原同名即冲突() -> None:
    group = build_group(
        build_requirement("花生"),
        build_requirement("鸡蛋"),
    )
    merged = build_merged(
        dishes=[build_dish(required_ingredient_groups=[group])]
    )

    result = ConstraintIntegrationService().integrate(
        build_profile(allergens=["花生"]),
        merged,
    )

    assert result["has_conflicts"] is True
    assert [item["allergen"] for item in result["conflicts"]] == ["花生"]
    assert result["conflicts"][0]["dialogue_path"] == (
        "dishes[0].required_ingredient_groups[0].items[0].value"
    )


def test_any组仍有安全选项时不在整合层报冲突() -> None:
    group = build_group(
        build_requirement("花生"),
        build_requirement("鸡蛋"),
        match="any",
    )
    merged = build_merged(
        dishes=[build_dish(required_ingredient_groups=[group])]
    )

    result = ConstraintIntegrationService().integrate(
        build_profile(allergens=["花生"]),
        merged,
    )

    assert result["has_conflicts"] is False
    assert result["conflicts"] == []


def test_any组全部选项与过敏原同名时返回每个冲突项() -> None:
    group = build_group(
        build_requirement("花生"),
        build_requirement("鸡蛋"),
        match="any",
    )
    merged = build_merged(
        dishes=[build_dish(required_ingredient_groups=[group])]
    )

    result = ConstraintIntegrationService().integrate(
        build_profile(allergens=["花生", "鸡蛋"]),
        merged,
    )

    assert result["has_conflicts"] is True
    assert [item["allergen"] for item in result["conflicts"]] == [
        "花生",
        "鸡蛋",
    ]


@pytest.mark.parametrize(
    "group, expected_text",
    [
        ({"match": "all", "items": []}, "all组至少包含1项"),
        (
            {"match": "any", "items": [build_requirement()]},
            "any组至少包含2项",
        ),
        ({"match": "some", "items": [build_requirement()]}, "match"),
        (
            {
                "match": "all",
                "items": [build_requirement(), build_requirement()],
            },
            "重复",
        ),
        (
            {
                "match": "all",
                "items": [build_requirement(kind="raw")],
            },
            "kind",
        ),
    ],
)
def test_非法食材分组在整合入口返回400(
    group: dict[str, Any],
    expected_text: str,
) -> None:
    merged = build_merged(
        dishes=[build_dish(required_ingredient_groups=[group])]
    )

    with pytest.raises(Exception) as captured:
        ConstraintIntegrationService().integrate(build_profile(), merged)

    assert getattr(captured.value, "status_code", None) == 400
    assert expected_text in str(captured.value)


def test_旧食材数组不再被约束整合接受() -> None:
    dish = build_dish()
    del dish["required_ingredient_groups"]
    dish["required_ingredients"] = [build_requirement()]
    merged = build_merged(
        dishes=[dish],
        evidence={
            "dishes[0].required_ingredients[0].value": "番茄"
        },
    )

    with pytest.raises(Exception) as captured:
        ConstraintIntegrationService().integrate(build_profile(), merged)

    assert getattr(captured.value, "status_code", None) == 400


def test_筛选查询将组间组合为AND且all组内全部满足() -> None:
    groups = [
        build_group(
            build_requirement("番茄"),
            build_requirement("鸡蛋"),
        ),
        build_group(build_requirement("面", kind="concept")),
    ]
    constraints = build_integrated(
        meal_periods=["午餐"],
        dishes=[build_integrated_dish(required_ingredient_groups=groups)],
    )
    driver = FakeDriver()

    DishFilteringService(driver).filter(constraints)

    query, params = driver.calls[-1]
    assert "$req_0_0" in query
    assert "$req_0_1" in query
    assert "$req_1_0" in query
    assert " OR " not in query.split("$req_0_0", 1)[1].split(
        "$req_1_0", 1
    )[0]
    assert params["req_0_0"] == "番茄"
    assert params["req_0_1"] == "鸡蛋"
    assert params["req_1_0"] == "面"


def test_筛选查询将any组内项目组合为OR() -> None:
    group = build_group(
        build_requirement("鱼"),
        build_requirement("鸡翅"),
        match="any",
    )
    constraints = build_integrated(
        dishes=[build_integrated_dish(required_ingredient_groups=[group])]
    )
    driver = FakeDriver()

    DishFilteringService(driver).filter(constraints)

    query, params = driver.calls[-1]
    assert "$req_0_0" in query and "$req_0_1" in query
    assert " OR " in query
    assert params["req_0_0"] == "鱼"
    assert params["req_0_1"] == "鸡翅"


def test_筛选入口拒绝旧食材数组() -> None:
    dish = build_integrated_dish()
    del dish["required_ingredient_groups"]
    dish["required_ingredients"] = []
    constraints = build_integrated(dishes=[dish])

    with pytest.raises(Exception) as captured:
        DishFilteringService(FakeDriver()).filter(constraints)

    assert getattr(captured.value, "status_code", None) == 400
