from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any, Callable, cast

from backend.core.menu_planning_contract import NUTRIENT_FIELDS
from backend.core.menu_recommendation_contract import (
    CandidateAttempt,
    MenuGenerationResult,
    MenuRecommendationError,
    QualityWarning,
)


NUTRITION_TARGET_SCORE = 8
CANDIDATE_LIMITS = (100, 300)


class MenuRecommendationService:
    """从持久化会话生成最终菜单和推荐理由。"""

    def __init__(
        self,
        *,
        confirmation_service: object,
        profile_service: object,
        integration_service: object,
        filtering_service: object,
        nutrition_service: object,
        planning_service: object,
        reason_service: object,
    ) -> None:
        dependencies = (
            (confirmation_service, "get_session", "约束确认Service无效"),
            (profile_service, "extract", "档案约束Service无效"),
            (integration_service, "integrate", "约束整合Service无效"),
            (filtering_service, "filter", "菜品筛选Service无效"),
            (
                nutrition_service,
                "get_recipe_nutrition",
                "营养Service无效",
            ),
            (
                nutrition_service,
                "get_meal_nutrition_targets",
                "营养Service无效",
            ),
            (planning_service, "plan", "菜单规划Service无效"),
            (reason_service, "build", "推荐理由Service无效"),
        )
        for dependency, method, message in dependencies:
            if not callable(getattr(dependency, method, None)):
                raise MenuRecommendationError(500, message)
        self._confirmation_service = confirmation_service
        self._profile_service = profile_service
        self._integration_service = integration_service
        self._filtering_service = filtering_service
        self._nutrition_service = nutrition_service
        self._planning_service = planning_service
        self._reason_service = reason_service

    def generate(self, session_id: object) -> MenuGenerationResult:
        """读取指定会话并运行确定性的完整推荐链路。"""

        validated_session_id = _validate_session_id(session_id)
        confirmation_state = self._call(
            lambda: self._confirmation_service.get_session(
                validated_session_id
            ),
            allow_bad_request=True,
        )
        confirmation = _require_mapping(
            confirmation_state,
            "约束确认结果无效",
        )
        profile_id = _require_positive_integer(
            confirmation.get("profile_id"),
            "约束确认结果缺少有效profile_id",
        )
        status = confirmation.get("status")
        merged = confirmation.get("merged_constraints")
        dialogue_id = validated_session_id
        if isinstance(merged, Mapping):
            dialogue_id = _require_positive_integer(
                merged.get("dialogue_id"),
                "会话约束缺少有效dialogue_id",
            )
        result = _build_empty_result(
            validated_session_id,
            profile_id,
            dialogue_id,
            copy.deepcopy(dict(confirmation)),
        )
        if status == "in_progress":
            result["status"] = "in_progress"
            return result
        if status == "needs_confirmation":
            result["status"] = "needs_confirmation"
            return result
        if status != "ready_for_planning" or not isinstance(merged, Mapping):
            raise MenuRecommendationError(500, "约束确认状态无效")

        planning_context = _require_mapping(
            confirmation.get("planning_context"),
            "规划上下文无效",
        )
        meal_period = planning_context.get("meal_period")
        if meal_period not in {"早餐", "午餐", "晚餐"}:
            raise MenuRecommendationError(500, "规划餐次无效")
        diner_count = _require_positive_integer(
            planning_context.get("diner_count"),
            "规划人数无效",
        )
        total_dish_count = _require_positive_integer(
            planning_context.get("total_dish_count"),
            "规划菜品数无效",
        )

        profile = self._call(
            lambda: self._profile_service.extract(profile_id)
        )
        profile_mapping = _require_mapping(profile, "档案约束结果无效")
        integrated = self._call(
            lambda: self._integration_service.integrate(
                profile_mapping,
                merged,
            )
        )
        integrated_mapping = _require_mapping(
            integrated,
            "约束整合结果无效",
        )
        conflicts = integrated_mapping.get("conflicts")
        if not isinstance(conflicts, list):
            raise MenuRecommendationError(500, "约束冲突结果无效")
        if integrated_mapping.get("has_conflicts"):
            result["status"] = "constraint_conflict"
            result["conflicts"] = copy.deepcopy(conflicts)
            return result

        effective_constraints = copy.deepcopy(dict(integrated_mapping))
        effective_constraints["meal_periods"] = [meal_period]
        filtering = self._call(
            lambda: self._filtering_service.filter(effective_constraints)
        )
        filtering_mapping = _require_mapping(
            filtering,
            "菜品筛选结果无效",
        )
        filtering_result = copy.deepcopy(dict(filtering_mapping))
        result["dish_filtering_result"] = cast(Any, filtering_result)
        unmatched = filtering_result.get("unmatched_allergens")
        dish_candidates = filtering_result.get("dishes")
        if not isinstance(unmatched, list) or not isinstance(
            dish_candidates,
            list,
        ):
            raise MenuRecommendationError(500, "菜品筛选结果结构无效")
        integrated_dishes = effective_constraints.get("dishes")
        if (
            not isinstance(integrated_dishes, list)
            or len(dish_candidates) != len(integrated_dishes)
            or any(
                not isinstance(candidates, list)
                for candidates in dish_candidates
            )
        ):
            raise MenuRecommendationError(500, "筛选候选组与整合约束不一致")
        if unmatched:
            result["status"] = "unmatched_allergen"
            result["unmatched_allergens"] = copy.deepcopy(unmatched)
            return result
        empty_indexes = [
            index
            for index, candidates in enumerate(dish_candidates)
            if not candidates
        ]
        if empty_indexes:
            result["status"] = "empty_candidate"
            result["empty_dish_indexes"] = empty_indexes
            return result

        nutrition_by_name = self._load_nutrition(dish_candidates)
        targets = self._call(
            lambda: self._nutrition_service.get_meal_nutrition_targets(
                profile_id,
                meal_period,
            )
        )
        target_mapping = _require_mapping(targets, "单餐营养目标结果无效")
        nutrients = target_mapping.get("nutrients")
        if not isinstance(nutrients, Mapping):
            raise MenuRecommendationError(500, "单餐营养目标不完整")
        special_populations = profile_mapping.get("special_populations")
        if not isinstance(special_populations, list):
            raise MenuRecommendationError(500, "档案特殊人群约束无效")

        final_planning_result: dict[str, Any] | None = None
        attempts: list[CandidateAttempt] = []
        for candidate_limit, staged_candidates in _candidate_stages(
            dish_candidates
        ):
            planning_input = _build_planning_input(
                effective_constraints,
                staged_candidates,
                nutrition_by_name,
                nutrients,
                meal_period=meal_period,
                diner_count=diner_count,
                total_dish_count=total_dish_count,
                special_populations=special_populations,
            )
            try:
                planned = self._planning_service.plan(planning_input)
            except Exception as exc:
                if getattr(exc, "status_code", None) == 422:
                    attempts.append(
                        {
                            "candidate_limit": candidate_limit,
                            "candidate_counts": [
                                len(candidates)
                                for candidates in staged_candidates
                            ],
                            "outcome": "infeasible",
                            "nutrition_score": None,
                        }
                    )
                    continue
                raise _dependency_error(exc) from exc
            planned_mapping = _require_mapping(
                planned,
                "菜单规划结果无效",
            )
            score = planned_mapping.get("nutrition_score")
            if type(score) is not int or not 0 <= score <= 16:
                raise MenuRecommendationError(500, "菜单营养得分无效")
            is_full = candidate_limit is None
            outcome = (
                "accepted"
                if score >= NUTRITION_TARGET_SCORE or is_full
                else "below_target"
            )
            attempts.append(
                {
                    "candidate_limit": candidate_limit,
                    "candidate_counts": [
                        len(candidates) for candidates in staged_candidates
                    ],
                    "outcome": outcome,
                    "nutrition_score": score,
                }
            )
            if outcome == "accepted":
                final_planning_result = copy.deepcopy(dict(planned_mapping))
                break

        result["candidate_attempts"] = attempts
        if final_planning_result is None:
            result["status"] = "planning_infeasible"
            return result

        reasons = self._call(
            lambda: self._reason_service.build(
                filtering_result,
                final_planning_result,
            )
        )
        reason_mapping = _require_mapping(reasons, "推荐理由结果无效")
        score = final_planning_result["nutrition_score"]
        warnings: list[QualityWarning] = []
        if score < NUTRITION_TARGET_SCORE:
            warnings.append(
                {
                    "code": "nutrition_score_below_target",
                    "nutrition_score": score,
                    "target_score": NUTRITION_TARGET_SCORE,
                }
            )
        result["status"] = "recommended"
        result["menu_planning_result"] = cast(Any, final_planning_result)
        result["recommendation_reason_result"] = cast(
            Any,
            copy.deepcopy(dict(reason_mapping)),
        )
        result["quality_warnings"] = warnings
        return result

    def _load_nutrition(
        self,
        dish_candidates: list[Any],
    ) -> dict[str, dict[str, Any]]:
        names = list(
            dict.fromkeys(
                candidate["recipe_name"]
                for candidates in dish_candidates
                for candidate in candidates
            )
        )
        loaded = self._call(
            lambda: self._nutrition_service.get_recipe_nutrition(names)
        )
        if not isinstance(loaded, list):
            raise MenuRecommendationError(500, "菜谱营养结果无效")
        by_name: dict[str, dict[str, Any]] = {}
        for value in loaded:
            item = _require_mapping(value, "菜谱营养条目无效")
            name = item.get("recipe_name")
            if not isinstance(name, str) or name in by_name:
                raise MenuRecommendationError(500, "菜谱营养名称无效")
            by_name[name] = dict(item)
        if list(by_name) != names:
            raise MenuRecommendationError(500, "菜谱营养结果与候选不一致")
        return by_name

    @staticmethod
    def _call(
        action: Callable[[], object],
        *,
        allow_bad_request: bool = False,
    ) -> object:
        try:
            return action()
        except MenuRecommendationError:
            raise
        except Exception as exc:
            if allow_bad_request and getattr(exc, "status_code", None) == 400:
                raise MenuRecommendationError(400, str(exc)) from exc
            raise _dependency_error(exc) from exc


def _candidate_stages(
    dish_candidates: list[list[dict[str, Any]]],
) -> list[tuple[int | None, list[list[dict[str, Any]]]]]:
    stages: list[tuple[int | None, list[list[dict[str, Any]]]]] = []
    for limit in CANDIDATE_LIMITS:
        staged = [candidates[:limit] for candidates in dish_candidates]
        if all(
            len(staged_group) == len(full_group)
            for staged_group, full_group in zip(
                staged,
                dish_candidates,
                strict=True,
            )
        ):
            stages.append((None, staged))
            return stages
        stages.append((limit, staged))
    stages.append((None, copy.deepcopy(dish_candidates)))
    return stages


def _build_planning_input(
    effective_constraints: dict[str, Any],
    staged_candidates: list[list[dict[str, Any]]],
    nutrition_by_name: dict[str, dict[str, Any]],
    nutrients: Mapping[str, Any],
    *,
    meal_period: str,
    diner_count: int,
    total_dish_count: int,
    special_populations: list[str],
) -> dict[str, Any]:
    dishes = []
    for dish, candidates in zip(
        effective_constraints["dishes"],
        staged_candidates,
        strict=True,
    ):
        planning_candidates = []
        for candidate in candidates:
            nutrition = nutrition_by_name[candidate["recipe_name"]]
            planning_candidates.append(
                {
                    "recipe_name": candidate["recipe_name"],
                    "recipe_type": candidate["recipe_type"],
                    "matched_tags": list(candidate["matched_tags"]),
                    "nutrition": {
                        field: nutrition[field]
                        for field in NUTRIENT_FIELDS
                    },
                }
            )
        dishes.append(
            {
                "count": dish["count"],
                "dish_type": dish["dish_type"],
                "candidates": planning_candidates,
            }
        )
    return {
        "profile_id": effective_constraints["profile_id"],
        "dialogue_id": effective_constraints["dialogue_id"],
        "meal_period": meal_period,
        "diner_count": diner_count,
        "total_dish_count": total_dish_count,
        "special_populations": copy.deepcopy(special_populations),
        "dishes": dishes,
        "nutrient_targets": copy.deepcopy(dict(nutrients)),
        "unmatched_allergens": [],
    }


def _build_empty_result(
    session_id: int,
    profile_id: int,
    dialogue_id: int,
    confirmation_state: dict[str, Any],
) -> MenuGenerationResult:
    return {
        "session_id": session_id,
        "profile_id": profile_id,
        "dialogue_id": dialogue_id,
        "status": "in_progress",
        "confirmation_state": confirmation_state,
        "conflicts": [],
        "unmatched_allergens": [],
        "empty_dish_indexes": [],
        "dish_filtering_result": None,
        "candidate_attempts": [],
        "menu_planning_result": None,
        "recommendation_reason_result": None,
        "quality_warnings": [],
    }


def _validate_session_id(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise MenuRecommendationError(400, "session_id必须是正整数")
    return value


def _require_mapping(value: object, message: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MenuRecommendationError(500, message)
    return value


def _require_positive_integer(value: object, message: str) -> int:
    if type(value) is not int or value <= 0:
        raise MenuRecommendationError(500, message)
    return value


def _dependency_error(exc: Exception) -> MenuRecommendationError:
    return MenuRecommendationError(500, str(exc) or "推荐链路依赖调用失败")


__all__ = ["MenuRecommendationError", "MenuRecommendationService"]
