from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any, Callable, TypedDict, cast

from backend.core.menu_change_contract import (
    MenuChangeIntent,
    MenuChangePolicy,
    MenuChangeResult,
)
from backend.core.menu_planning_contract import NUTRIENT_FIELDS
from backend.core.menu_recommendation_contract import (
    CandidateAttempt,
    MenuGenerationResult,
    MenuRecommendationError,
    QualityWarning,
)
from backend.services.menu_change import (
    MenuChangeError,
    filter_excluded_candidates,
    validate_menu_change,
)


NUTRITION_TARGET_SCORE = 8
CANDIDATE_LIMITS = (100, 300)


class _MenuChangeContext(TypedDict):
    intent: MenuChangeIntent
    policy: MenuChangePolicy | None
    excluded_recipe_names: list[str]


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
            (
                confirmation_service,
                "save_menu_result",
                "约束确认Service无效",
            ),
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
        menu_change_context = _read_menu_change_context(confirmation)
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
            result["dish_filtering_result"] = cast(Any, filtering_result)
            result["status"] = "unmatched_allergen"
            result["unmatched_allergens"] = copy.deepcopy(unmatched)
            return result
        dish_candidates = _filter_menu_change_candidates(
            dish_candidates,
            menu_change_context,
        )
        filtering_result["dishes"] = dish_candidates
        result["dish_filtering_result"] = cast(Any, filtering_result)
        empty_indexes = [
            index
            for index, candidates in enumerate(dish_candidates)
            if not candidates
        ]
        if empty_indexes:
            if menu_change_context["intent"]["mode"] == "none":
                result["status"] = "empty_candidate"
                result["empty_dish_indexes"] = empty_indexes
            else:
                result["status"] = "planning_infeasible"
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
                menu_change_policy=menu_change_context["policy"],
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

        menu_change_result = self._save_menu_result(
            validated_session_id,
            final_planning_result,
        )
        if menu_change_result is not None:
            result["menu_change_result"] = cast(
                Any,
                menu_change_result,
            )

        reasons = self._call(
            lambda: self._reason_service.build(
                filtering_result,
                final_planning_result,
                {
                    "effective_constraints": copy.deepcopy(
                        effective_constraints
                    ),
                    "candidate_attempts": copy.deepcopy(attempts),
                },
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

    def _save_menu_result(
        self,
        session_id: int,
        planning_result: Mapping[str, Any],
    ) -> MenuChangeResult | None:
        menu_state = self._call(
            lambda: self._confirmation_service.save_menu_result(
                session_id,
                _build_menu_snapshot(planning_result),
            )
        )
        menu_state_mapping = _require_mapping(
            menu_state,
            "菜单状态保存结果无效",
        )
        menu_change_result = menu_state_mapping.get("menu_change_result")
        if menu_change_result is None:
            return None
        if not isinstance(menu_change_result, Mapping):
            raise MenuRecommendationError(500, "换菜结算结果无效")
        return cast(
            MenuChangeResult,
            copy.deepcopy(dict(menu_change_result)),
        )

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


def _read_menu_change_context(
    confirmation: Mapping[str, Any],
) -> _MenuChangeContext:
    try:
        intent = validate_menu_change(confirmation.get("menu_change"))
    except MenuChangeError as exc:
        raise MenuRecommendationError(500, str(exc)) from exc
    raw_policy = confirmation.get("menu_change_policy")
    if raw_policy is not None and not isinstance(raw_policy, Mapping):
        raise MenuRecommendationError(500, "菜单操作求解约束无效")
    policy = (
        cast(MenuChangePolicy, copy.deepcopy(dict(raw_policy)))
        if raw_policy is not None
        else None
    )
    return {
        "intent": intent,
        "policy": policy,
        "excluded_recipe_names": _require_recipe_names(
            confirmation.get("excluded_recipe_names"),
            "会话排除菜状态无效",
        ),
    }


def _filter_menu_change_candidates(
    dish_candidates: list[Any],
    context: _MenuChangeContext,
) -> list[list[dict[str, Any]]]:
    restored_name = (
        context["intent"]["replacement_recipe_name"]
        if context["intent"]["mode"] == "restore_specific"
        else None
    )
    effective_exclusions = [
        name
        for name in context["excluded_recipe_names"]
        if name != restored_name
    ]
    try:
        return filter_excluded_candidates(
            dish_candidates,
            effective_exclusions,
        )
    except MenuChangeError as exc:
        raise MenuRecommendationError(500, str(exc)) from exc


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
    menu_change_policy: MenuChangePolicy | None,
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
        "menu_change_policy": copy.deepcopy(menu_change_policy),
    }


def _build_menu_snapshot(
    planning_result: Mapping[str, Any],
) -> list[dict[str, Any]]:
    selected = planning_result.get("selected_dishes")
    if not isinstance(selected, list) or not selected:
        raise MenuRecommendationError(500, "菜单规划结果缺少已选菜品")
    snapshot: list[dict[str, Any]] = []
    for position, raw_item in enumerate(selected, start=1):
        item = _require_mapping(raw_item, "已选菜品结构无效")
        dish_index = item.get("dish_constraint_index")
        recipe_name = item.get("recipe_name")
        if type(dish_index) is not int or dish_index < 0:
            raise MenuRecommendationError(500, "已选菜品约束索引无效")
        if not isinstance(recipe_name, str) or not recipe_name:
            raise MenuRecommendationError(500, "已选菜品名称无效")
        snapshot.append(
            {
                "position": position,
                "dish_constraint_index": dish_index,
                "recipe_name": recipe_name,
            }
        )
    return snapshot


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


def _require_recipe_names(value: object, message: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise MenuRecommendationError(500, message)
    if len(value) != len(set(value)):
        raise MenuRecommendationError(500, message)
    return list(value)


def _dependency_error(exc: Exception) -> MenuRecommendationError:
    return MenuRecommendationError(500, str(exc) or "推荐链路依赖调用失败")


__all__ = ["MenuRecommendationError", "MenuRecommendationService"]
