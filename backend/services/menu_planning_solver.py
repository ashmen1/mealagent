from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from ortools.sat.python import cp_model

from backend.core.menu_nutrition_policy import build_nutrient_grade_bands
from backend.core.menu_planning_contract import (
    MenuPlanningError,
    MenuPlanningInput,
    NUTRIENT_FIELDS,
    PlanningCandidate,
)


SolverRunner = Callable[[cp_model.CpModel, float], object]
SCALE = Decimal("100")
SOLVE_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class CandidateSelection:
    dish_index: int
    candidate: PlanningCandidate
    variable: cp_model.IntVar


@dataclass(frozen=True)
class _PlanningModel:
    model: cp_model.CpModel
    candidates: list[CandidateSelection]
    score_expression: Any
    bad_expression: Any
    tag_expression: Any


def solve_menu(
    planning_input: MenuPlanningInput,
    diners: int,
    runner: SolverRunner,
) -> list[CandidateSelection]:
    planning_model = _build_model(planning_input, diners)
    selected_indexes = _solve_lexicographically(planning_model, runner)
    return [
        planning_model.candidates[index] for index in selected_indexes
    ]


def default_solver_runner(
    model: cp_model.CpModel,
    timeout_seconds: float,
) -> dict[str, Any]:
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = timeout_seconds
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    status = solver.solve(model)
    return {
        "status": solver.status_name(status),
        "value": solver.value,
    }


def _build_model(
    planning_input: MenuPlanningInput,
    diners: int,
) -> _PlanningModel:
    model = cp_model.CpModel()
    candidates: list[CandidateSelection] = []
    variables_by_dish: list[list[cp_model.IntVar]] = []
    variables_by_name: dict[str, list[cp_model.IntVar]] = defaultdict(list)

    for dish_index, dish in enumerate(planning_input["dishes"]):
        dish_variables: list[cp_model.IntVar] = []
        for candidate_index, candidate in enumerate(dish["candidates"]):
            variable = model.new_bool_var(
                f"select_{dish_index}_{candidate_index}"
            )
            dish_variables.append(variable)
            variables_by_name[candidate["recipe_name"]].append(variable)
            candidates.append(
                CandidateSelection(dish_index, candidate, variable)
            )
        variables_by_dish.append(dish_variables)

    _add_dish_count_constraints(
        model,
        planning_input,
        diners,
        variables_by_dish,
        candidates,
    )
    for same_name_variables in variables_by_name.values():
        model.add(sum(same_name_variables) <= 1)

    totals = _add_nutrition_totals(model, candidates)
    _add_nutrition_hard_constraints(
        model,
        totals,
        planning_input,
        diners,
    )
    score_expression, bad_expression = _add_grade_expressions(
        model,
        totals,
        planning_input,
        diners,
    )
    tag_expression = sum(
        len(item.candidate["matched_tags"]) * item.variable
        for item in candidates
    )
    return _PlanningModel(
        model=model,
        candidates=candidates,
        score_expression=score_expression,
        bad_expression=bad_expression,
        tag_expression=tag_expression,
    )


def _add_dish_count_constraints(
    model: cp_model.CpModel,
    planning_input: MenuPlanningInput,
    diners: int,
    variables_by_dish: list[list[cp_model.IntVar]],
    candidates: list[CandidateSelection],
) -> None:
    has_unspecified_count = False
    for dish, variables in zip(
        planning_input["dishes"], variables_by_dish, strict=True
    ):
        if dish["count"] is None:
            has_unspecified_count = True
            model.add(sum(variables) >= 1)
        else:
            model.add(sum(variables) == dish["count"])

    if planning_input["total_dish_count"] is not None:
        model.add(
            sum(item.variable for item in candidates)
            == planning_input["total_dish_count"]
        )
    elif has_unspecified_count:
        default_count = diners if diners <= 3 else diners - 1
        model.add(
            sum(item.variable for item in candidates) == default_count
        )


def _add_nutrition_totals(
    model: cp_model.CpModel,
    candidates: list[CandidateSelection],
) -> dict[str, cp_model.IntVar]:
    totals: dict[str, cp_model.IntVar] = {}
    for nutrient in NUTRIENT_FIELDS:
        coefficients = [
            _scaled(item.candidate["nutrition"][nutrient])
            for item in candidates
        ]
        total = model.new_int_var(
            0,
            sum(coefficients),
            f"total_{nutrient}",
        )
        model.add(
            total
            == sum(
                coefficient * item.variable
                for coefficient, item in zip(
                    coefficients, candidates, strict=True
                )
            )
        )
        totals[nutrient] = total
    return totals


def _add_nutrition_hard_constraints(
    model: cp_model.CpModel,
    totals: dict[str, cp_model.IntVar],
    planning_input: MenuPlanningInput,
    diners: int,
) -> None:
    targets = planning_input["nutrient_targets"]
    if "高血压" in planning_input["special_populations"]:
        upper_bound = targets["sodium_mg"]["upper_bound"]
        if upper_bound is None:
            raise MenuPlanningError(400, "sodium_mg缺少PI上限")
        model.add(totals["sodium_mg"] <= _scaled(upper_bound * diners))

    if "高血糖" in planning_input["special_populations"]:
        energy = totals["energy_kcal"]
        protein = totals["protein_g"]
        fat = totals["fat_g"]
        carbohydrate = totals["carbohydrate_g"]
        model.add(4 * 100 * protein >= 15 * energy)
        model.add(4 * 100 * protein <= 20 * energy)
        model.add(9 * 100 * fat >= 20 * energy)
        model.add(9 * 100 * fat <= 35 * energy)
        model.add(4 * 100 * carbohydrate >= 45 * energy)
        model.add(4 * 100 * carbohydrate <= 60 * energy)


def _add_grade_expressions(
    model: cp_model.CpModel,
    totals: dict[str, cp_model.IntVar],
    planning_input: MenuPlanningInput,
    diners: int,
) -> tuple[Any, Any]:
    score_parts: list[Any] = []
    bad_parts: list[Any] = []
    bands = build_nutrient_grade_bands(
        planning_input["nutrient_targets"], diners
    )
    for nutrient, band in bands.items():
        name = nutrient.partition("_")[0]
        is_normal = _add_range_indicator(
            model,
            totals[nutrient],
            _scale_lower_bound(band.normal_lower),
            _scale_upper_bound(band.normal_upper),
            f"{name}_normal",
        )
        is_excellent = _add_range_indicator(
            model,
            totals[nutrient],
            _scale_lower_bound(band.excellent_lower),
            _scale_upper_bound(band.excellent_upper),
            f"{name}_excellent",
        )
        score_parts.append(is_normal + is_excellent)
        bad_parts.append(1 - is_normal)
    return sum(score_parts), sum(bad_parts)


def _add_range_indicator(
    model: cp_model.CpModel,
    expression: cp_model.IntVar,
    lower: int | None,
    upper: int | None,
    name: str,
) -> cp_model.IntVar:
    bounds = []
    if lower is not None:
        bounds.append(
            _add_at_least_indicator(
                model, expression, lower, f"{name}_lower"
            )
        )
    if upper is not None:
        bounds.append(
            _add_at_most_indicator(
                model, expression, upper, f"{name}_upper"
            )
        )
    if len(bounds) == 1:
        return bounds[0]
    indicator = model.new_bool_var(name)
    model.add_min_equality(indicator, bounds)
    return indicator


def _add_at_least_indicator(
    model: cp_model.CpModel,
    expression: cp_model.IntVar,
    threshold: int,
    name: str,
) -> cp_model.IntVar:
    indicator = model.new_bool_var(name)
    model.add(expression >= threshold).only_enforce_if(indicator)
    model.add(expression < threshold).only_enforce_if(indicator.Not())
    return indicator


def _add_at_most_indicator(
    model: cp_model.CpModel,
    expression: cp_model.IntVar,
    threshold: int,
    name: str,
) -> cp_model.IntVar:
    indicator = model.new_bool_var(name)
    model.add(expression <= threshold).only_enforce_if(indicator)
    model.add(expression > threshold).only_enforce_if(indicator.Not())
    return indicator


def _solve_lexicographically(
    planning_model: _PlanningModel,
    runner: SolverRunner,
) -> list[int]:
    model = planning_model.model
    started_at = time.monotonic()
    is_first_solve = True

    def solve() -> object:
        nonlocal is_first_solve
        if is_first_solve:
            timeout_seconds: float = SOLVE_TIMEOUT_SECONDS
            is_first_solve = False
        else:
            timeout_seconds = SOLVE_TIMEOUT_SECONDS - (
                time.monotonic() - started_at
            )
        if timeout_seconds <= 0:
            raise MenuPlanningError(503, "10秒内未证明菜单最优")
        try:
            result = runner(model, timeout_seconds)
        except Exception as exc:
            raise MenuPlanningError(500, "CP-SAT求解执行失败") from exc
        _require_optimal_status(result)
        return result

    model.maximize(planning_model.score_expression)
    result = solve()
    best_score = _result_value(result, planning_model.score_expression)
    model.add(planning_model.score_expression == best_score)

    model.minimize(planning_model.bad_expression)
    result = solve()
    best_bad_count = _result_value(result, planning_model.bad_expression)
    model.add(planning_model.bad_expression == best_bad_count)

    model.maximize(planning_model.tag_expression)
    result = solve()
    best_tag_count = _result_value(result, planning_model.tag_expression)
    model.add(planning_model.tag_expression == best_tag_count)

    for candidate in planning_model.candidates:
        model.maximize(candidate.variable)
        result = solve()
        selected = _result_value(result, candidate.variable)
        model.add(candidate.variable == selected)

    return [
        index
        for index, candidate in enumerate(planning_model.candidates)
        if _result_value(result, candidate.variable) == 1
    ]


def _require_optimal_status(result: object) -> None:
    status = _result_status(result)
    if status == "OPTIMAL":
        return
    if status == "INFEASIBLE":
        raise MenuPlanningError(422, "候选数量或营养硬约束无解")
    if status in {"FEASIBLE", "UNKNOWN"}:
        raise MenuPlanningError(503, "10秒内未证明菜单最优")
    if status == "MODEL_INVALID":
        raise MenuPlanningError(500, "CP-SAT模型非法")
    raise MenuPlanningError(500, f"CP-SAT返回未知状态：{status}")


def _result_status(result: object) -> str:
    if isinstance(result, Mapping):
        status = result.get("status")
    else:
        status = getattr(result, "status", None)
    if isinstance(status, str):
        return status.upper()
    if type(status) is int:
        return cp_model.CpSolver().status_name(status).upper()
    raise MenuPlanningError(500, "CP-SAT求解结果缺少状态")


def _result_value(result: object, expression: Any) -> int:
    if isinstance(expression, int):
        return expression
    if isinstance(result, Mapping):
        value_reader = result.get("value")
    else:
        value_reader = getattr(result, "value", None)
    if not callable(value_reader):
        raise MenuPlanningError(500, "CP-SAT最优结果缺少变量值")
    return int(value_reader(expression))


def _scaled(value: Decimal) -> int:
    return int(value * SCALE)


def _scale_lower_bound(value: Decimal | None) -> int | None:
    if value is None:
        return None
    return int((value * SCALE).to_integral_value(rounding=ROUND_CEILING))


def _scale_upper_bound(value: Decimal | None) -> int | None:
    if value is None:
        return None
    return int((value * SCALE).to_integral_value(rounding=ROUND_FLOOR))


__all__ = [
    "CandidateSelection",
    "SolverRunner",
    "default_solver_runner",
    "solve_menu",
]
