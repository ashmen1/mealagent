from __future__ import annotations

import copy
import importlib
from decimal import Decimal
from typing import Any, Callable

import pytest


NUTRIENT_FIELDS = (
    "energy_kcal",
    "protein_g",
    "fat_g",
    "carbohydrate_g",
    "fiber_g",
    "sodium_mg",
    "calcium_mg",
    "iron_mg",
    "cholesterol_mg",
)


class FakeDependencyError(Exception):
    """模拟依赖服务的带状态码异常。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def build_requirement(
    value: str = "番茄",
    kind: str = "ingredient",
) -> dict[str, str]:
    return {"kind": kind, "value": value}


def build_group(
    *items: dict[str, str],
    match: str = "all",
) -> dict[str, Any]:
    return {
        "match": match,
        "items": list(items) if items else [build_requirement()],
    }


def build_dish(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "count": None,
        "dish_type": "未指定",
        "taste_preferences": {},
        "cuisines": [],
        "effects": [],
        "special_populations": [],
        "required_ingredient_groups": [],
    }
    result.update(copy.deepcopy(overrides))
    return result


def build_merged(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "dialogue_id": 101,
        "meal_periods": [],
        "diner_count": None,
        "total_dish_count": None,
        "max_total_time_minutes": None,
        "max_difficulty": None,
        "available_ingredients": [],
        "dishes": [build_dish()],
        "evidence": {},
    }
    result.update(copy.deepcopy(overrides))
    if "evidence" not in overrides:
        result["evidence"] = build_evidence(result)
    return result


def build_evidence(merged: dict[str, Any]) -> dict[str, str]:
    evidence: dict[str, str] = {}
    for index, value in enumerate(merged["meal_periods"]):
        evidence[f"meal_periods[{index}]"] = value
    for field in (
        "diner_count",
        "total_dish_count",
        "max_total_time_minutes",
        "max_difficulty",
    ):
        if merged[field] is not None:
            evidence[field] = str(merged[field])
    for index, value in enumerate(merged["available_ingredients"]):
        evidence[f"available_ingredients[{index}]"] = value
    for dish_index, dish in enumerate(merged["dishes"]):
        prefix = f"dishes[{dish_index}]"
        if dish["count"] is not None:
            evidence[f"{prefix}.count"] = str(dish["count"])
        if dish["dish_type"] != "未指定":
            evidence[f"{prefix}.dish_type"] = dish["dish_type"]
        for taste, enabled in dish["taste_preferences"].items():
            evidence[f"{prefix}.taste_preferences.{taste}"] = (
                taste if enabled else f"不{taste}"
            )
        for field in ("cuisines", "effects", "special_populations"):
            for index, value in enumerate(dish[field]):
                evidence[f"{prefix}.{field}[{index}]"] = value
        for group_index, group in enumerate(
            dish.get("required_ingredient_groups", [])
        ):
            group_prefix = (
                f"{prefix}.required_ingredient_groups[{group_index}]"
            )
            evidence[f"{group_prefix}.match"] = group["match"]
            for item_index, item in enumerate(group["items"]):
                evidence[f"{group_prefix}.items[{item_index}].value"] = item[
                    "value"
                ]
    return evidence


def build_profile(**overrides: Any) -> dict[str, Any]:
    result = {
        "profile_id": 25,
        "special_populations": [],
        "taste_preferences": {},
        "allergens": [],
    }
    result.update(copy.deepcopy(overrides))
    return result


def build_integrated_dish(**overrides: Any) -> dict[str, Any]:
    result = build_dish()
    result.update(copy.deepcopy(overrides))
    return result


def build_integrated(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "profile_id": 25,
        "dialogue_id": 101,
        "meal_periods": [],
        "diner_count": None,
        "total_dish_count": None,
        "max_total_time_minutes": None,
        "max_difficulty": None,
        "available_ingredients": [],
        "allergens": [],
        "dishes": [build_integrated_dish()],
        "has_conflicts": False,
        "conflicts": [],
    }
    result.update(copy.deepcopy(overrides))
    return result


def build_candidate(recipe_name: str, **overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "recipe_name": recipe_name,
        "recipe_type": "菜",
        "matched_tags": ["午餐"],
        "matched_groups": ["餐次"],
    }
    result.update(copy.deepcopy(overrides))
    return result


def build_filtering_result(*counts: int) -> dict[str, Any]:
    actual_counts = counts or (1,)
    return {
        "dishes": [
            [
                build_candidate(f"第{dish_index}组菜{candidate_index:03d}")
                for candidate_index in range(count)
            ]
            for dish_index, count in enumerate(actual_counts)
        ],
        "unmatched_allergens": [],
    }


def build_confirmation_state(
    status: str = "ready_for_planning",
    *,
    merged: dict[str, Any] | None = None,
    profile_id: int = 25,
    meal_period: str | None = "午餐",
    meal_period_source: str | None = "explicit",
) -> dict[str, Any]:
    active_merged = build_merged() if merged is None else merged
    if status == "in_progress":
        active_merged = None
        planning_context = None
    else:
        planning_context = {
            "meal_period": meal_period,
            "meal_period_source": meal_period_source,
            "diner_count": 1,
            "diner_count_source": "default",
            "total_dish_count": 1,
            "total_dish_count_source": "default",
        }
    return {
        "session_id": 101,
        "profile_id": profile_id,
        "status": status,
        "merged_constraints": active_merged,
        "planning_context": planning_context,
        "known_constraints": [],
        "confirmation": (
            {
                "reason": "未明确餐次",
                "options": ["早餐", "午餐", "晚餐"],
                "question": "请确认这次要安排早餐、午餐还是晚餐？",
            }
            if status == "needs_confirmation"
            else None
        ),
        "message": None,
    }


def build_planning_result(
    planning_input: dict[str, Any],
    score: int = 8,
) -> dict[str, Any]:
    selected = []
    for dish_index, dish in enumerate(planning_input["dishes"]):
        if dish["candidates"]:
            candidate = dish["candidates"][0]
            selected.append(
                {
                    "dish_constraint_index": dish_index,
                    "recipe_name": candidate["recipe_name"],
                    "recipe_type": candidate["recipe_type"],
                    "matched_tags": list(candidate["matched_tags"]),
                    "nutrition": dict(candidate["nutrition"]),
                }
            )
    return {
        "profile_id": planning_input["profile_id"],
        "dialogue_id": planning_input["dialogue_id"],
        "meal_period": planning_input["meal_period"],
        "diner_count": planning_input["diner_count"],
        "selected_dishes": selected,
        "nutrition_score": score,
        "nutrient_grades": {},
        "applied_health_constraints": [],
        "unapplied_health_constraints": [],
    }


class FakeConfirmationService:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[object] = []
        self.error: BaseException | None = None

    def get_session(self, session_id: object) -> dict[str, Any]:
        self.calls.append(session_id)
        if self.error is not None:
            raise self.error
        return copy.deepcopy(self.result)


class FakeProfileService:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = result or build_profile()
        self.calls: list[object] = []

    def extract(self, profile_id: object) -> dict[str, Any]:
        self.calls.append(profile_id)
        return copy.deepcopy(self.result)


class FakeIntegrationService:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = result or build_integrated()
        self.calls: list[tuple[object, object]] = []

    def integrate(self, profile: object, dialogue: object) -> dict[str, Any]:
        self.calls.append((copy.deepcopy(profile), copy.deepcopy(dialogue)))
        return copy.deepcopy(self.result)


class FakeFilteringService:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = result or build_filtering_result(1)
        self.calls: list[dict[str, Any]] = []

    def filter(self, constraints: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(copy.deepcopy(constraints))
        return copy.deepcopy(self.result)


class FakeNutritionService:
    def __init__(self) -> None:
        self.recipe_calls: list[list[str]] = []
        self.target_calls: list[tuple[int, str]] = []

    def get_recipe_nutrition(self, names: list[str]) -> list[dict[str, Any]]:
        self.recipe_calls.append(list(names))
        return [
            {
                "recipe_name": name,
                **{
                    field: Decimal(index + 1)
                    for index, field in enumerate(NUTRIENT_FIELDS)
                },
            }
            for name in names
        ]

    def get_meal_nutrition_targets(
        self,
        profile_id: int,
        meal_period: str,
    ) -> dict[str, Any]:
        self.target_calls.append((profile_id, meal_period))
        return {
            "profile_id": profile_id,
            "meal_period": meal_period,
            "nutrients": {
                field: {
                    "status": "available",
                    "unit": "g",
                    "target_value": Decimal("1"),
                    "lower_bound": Decimal("0"),
                    "upper_bound": Decimal("2"),
                    "target_basis": "测试",
                    "lower_basis": "测试",
                    "upper_basis": "测试",
                }
                for field in NUTRIENT_FIELDS
            },
        }


class FakePlanningService:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def plan(self, planning_input: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(copy.deepcopy(planning_input))
        response = self.responses.pop(0) if self.responses else 8
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            return response(planning_input)
        return build_planning_result(planning_input, int(response))


class FakeReasonService:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def build(
        self,
        filtering_result: dict[str, Any],
        planning_result: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(
            (copy.deepcopy(filtering_result), copy.deepcopy(planning_result))
        )
        return {
            "profile_id": planning_result["profile_id"],
            "dialogue_id": planning_result["dialogue_id"],
            "dish_recommendations": [],
            "menu_reasons": [],
        }


@pytest.fixture
def build_orchestrator() -> Callable[..., tuple[object, dict[str, Any]]]:
    def build(**overrides: Any) -> tuple[object, dict[str, Any]]:
        try:
            module = importlib.import_module(
                "backend.services.menu_recommendation"
            )
            service_class = module.MenuRecommendationService
        except (ModuleNotFoundError, AttributeError) as exc:
            pytest.fail(
                "缺少统一推荐入口 "
                "backend.services.menu_recommendation."
                f"MenuRecommendationService：{exc}",
                pytrace=False,
            )
        dependencies: dict[str, Any] = {
            "confirmation_service": FakeConfirmationService(
                build_confirmation_state()
            ),
            "profile_service": FakeProfileService(),
            "integration_service": FakeIntegrationService(),
            "filtering_service": FakeFilteringService(),
            "nutrition_service": FakeNutritionService(),
            "planning_service": FakePlanningService(),
            "reason_service": FakeReasonService(),
        }
        dependencies.update(overrides)
        return service_class(**dependencies), dependencies

    return build


__all__ = [
    "FakeConfirmationService",
    "FakeDependencyError",
    "FakeFilteringService",
    "FakeIntegrationService",
    "FakeNutritionService",
    "FakePlanningService",
    "FakeProfileService",
    "FakeReasonService",
    "build_candidate",
    "build_confirmation_state",
    "build_dish",
    "build_filtering_result",
    "build_group",
    "build_integrated",
    "build_integrated_dish",
    "build_merged",
    "build_profile",
    "build_requirement",
]
