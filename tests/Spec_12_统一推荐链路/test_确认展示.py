from __future__ import annotations

from typing import Any

from backend.services.constraint_confirmation import ConstraintConfirmationService

from .conftest import build_dish, build_group, build_merged, build_requirement


class FakeDialogueService:
    def __init__(self, merged: dict[str, Any]) -> None:
        self.merged = merged

    def create_session(self, profile_id: object) -> int:
        return 101

    def submit_turn(self, session_id: object, message: object) -> object:
        raise AssertionError("本测试不提交轮次")

    def get_session(self, session_id: object) -> dict[str, Any]:
        return {
            "session_id": 101,
            "profile_id": 25,
            "status": "ready_for_planning",
            "merged_constraints": self.merged,
            "missing_requirements": [],
        }


class FakeMealPeriodService:
    def resolve(self, meal_periods: object) -> dict[str, Any]:
        return {
            "status": "resolved",
            "meal_period": "午餐",
            "source": "explicit",
            "reason": None,
            "options": [],
        }


def test_确认展示按all_any和组间关系生成固定文本() -> None:
    groups = [
        build_group(
            build_requirement("番茄"),
            build_requirement("鸡蛋"),
        ),
        build_group(
            build_requirement("鱼"),
            build_requirement("鸡翅"),
            match="any",
        ),
    ]
    merged = build_merged(
        meal_periods=["午餐"],
        dishes=[build_dish(required_ingredient_groups=groups)],
    )
    service = ConstraintConfirmationService(
        FakeDialogueService(merged),
        FakeMealPeriodService(),
    )

    result = service.get_session(101)

    ingredient_constraint = next(
        item
        for item in result["known_constraints"]
        if item["label"] == "菜品组1所需食材"
    )
    assert ingredient_constraint == {
        "path": "dishes[0].required_ingredient_groups",
        "label": "菜品组1所需食材",
        "value": "番茄和鸡蛋；鱼或鸡翅",
        "source": "explicit",
    }


def test_单项all组只展示食材本身() -> None:
    merged = build_merged(
        meal_periods=["午餐"],
        dishes=[
            build_dish(
                required_ingredient_groups=[
                    build_group(build_requirement("西兰花"))
                ]
            )
        ],
    )
    service = ConstraintConfirmationService(
        FakeDialogueService(merged),
        FakeMealPeriodService(),
    )

    result = service.get_session(101)

    ingredient_constraint = next(
        item
        for item in result["known_constraints"]
        if item["label"] == "菜品组1所需食材"
    )
    assert ingredient_constraint["value"] == "西兰花"
