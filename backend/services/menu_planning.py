from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from backend.core.menu_nutrition_policy import grade_nutrients
from backend.core.menu_planning_contract import (
    MenuPlanningError,
    MenuPlanningInput,
    MenuPlanningResult,
    NUTRIENT_FIELDS,
    NutritionValues,
    PlannedDish,
)
from backend.core.menu_planning_validation import validate_menu_planning_input
from backend.services.menu_planning_solver import (
    CandidateSelection,
    SolverRunner,
    default_solver_runner,
    solve_menu,
)


TWO_PLACES = Decimal("0.01")
APPLIED_HEALTH_CONSTRAINTS = frozenset({"高血压", "高血糖"})
UNAPPLIED_HEALTH_CONSTRAINTS = frozenset({"高尿酸", "备孕"})


class MenuPlanningService:
    """使用 CP-SAT 选择唯一最优的固定配方菜单。"""

    def __init__(self, solver_runner: SolverRunner | None = None) -> None:
        if solver_runner is not None and not callable(solver_runner):
            raise MenuPlanningError(500, "CP-SAT求解器无效")
        self._solver_runner = solver_runner or default_solver_runner

    def plan(self, planning_input: object) -> MenuPlanningResult:
        normalized = validate_menu_planning_input(planning_input)
        _require_safe_and_nonempty_candidates(normalized)
        diners = normalized["diner_count"] or 1
        try:
            selected = solve_menu(
                normalized,
                diners,
                self._solver_runner,
            )
        except MenuPlanningError:
            raise
        except Exception as exc:
            raise MenuPlanningError(500, "CP-SAT模型构建或执行失败") from exc
        return _build_result(normalized, diners, selected)


def _require_safe_and_nonempty_candidates(
    planning_input: MenuPlanningInput,
) -> None:
    unmatched_allergens = planning_input["unmatched_allergens"]
    if unmatched_allergens:
        raise MenuPlanningError(
            422,
            "存在未解析过敏词：" + "、".join(unmatched_allergens),
        )

    empty_indexes = [
        index
        for index, dish in enumerate(planning_input["dishes"])
        if not dish["candidates"]
    ]
    if empty_indexes:
        raise MenuPlanningError(
            422,
            "菜品要求缺少候选："
            + "、".join(str(index) for index in empty_indexes),
        )


def _build_result(
    planning_input: MenuPlanningInput,
    diners: int,
    selected: list[CandidateSelection],
) -> MenuPlanningResult:
    total_nutrition = _sum_nutrition(selected)
    grades = grade_nutrients(
        total_nutrition,
        planning_input["nutrient_targets"],
        diners,
    )
    return {
        "profile_id": planning_input["profile_id"],
        "dialogue_id": planning_input["dialogue_id"],
        "meal_period": planning_input["meal_period"],
        "diner_count": diners,
        "selected_dishes": [_serialize_dish(item) for item in selected],
        "total_nutrition": total_nutrition,
        "per_person_nutrition": {
            nutrient: (total_nutrition[nutrient] / diners).quantize(
                TWO_PLACES,
                rounding=ROUND_HALF_UP,
            )
            for nutrient in NUTRIENT_FIELDS
        },
        "nutrient_grades": grades,
        "nutrition_score": sum(
            score
            for grade in grades.values()
            if (score := grade["score"]) is not None
        ),
        "applied_health_constraints": _filter_health_constraints(
            planning_input["special_populations"],
            APPLIED_HEALTH_CONSTRAINTS,
        ),
        "unapplied_health_constraints": _filter_health_constraints(
            planning_input["special_populations"],
            UNAPPLIED_HEALTH_CONSTRAINTS,
        ),
    }


def _serialize_dish(item: CandidateSelection) -> PlannedDish:
    candidate = item.candidate
    return {
        "dish_constraint_index": item.dish_index,
        "recipe_name": candidate["recipe_name"],
        "recipe_type": candidate["recipe_type"],
        "matched_tags": list(candidate["matched_tags"]),
        "nutrition": dict(candidate["nutrition"]),
    }


def _sum_nutrition(
    selected: list[CandidateSelection],
) -> NutritionValues:
    return {
        nutrient: sum(
            (item.candidate["nutrition"][nutrient] for item in selected),
            Decimal("0"),
        )
        for nutrient in NUTRIENT_FIELDS
    }


def _filter_health_constraints(
    populations: list[str],
    supported: frozenset[str],
) -> list[str]:
    return [population for population in populations if population in supported]


__all__ = ["MenuPlanningError", "MenuPlanningService"]
