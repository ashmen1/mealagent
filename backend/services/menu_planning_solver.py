from __future__ import annotations

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
from backend.core.recipe_pairing import PRIMARY_COOKING_METHODS


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
    composition_expression: Any
    temperature_expression: Any
    method_expression: Any
    selected_count: int
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
    _add_menu_change_constraints(
        model,
        planning_input,
        variables_by_name,
    )

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
    selected_count = _resolve_selected_count(planning_input, diners)
    composition_expression = _add_composition_expression(
        model,
        candidates,
        selected_count,
    )
    temperature_expression = _add_temperature_expression(
        model,
        candidates,
        selected_count,
    )
    method_expression = _add_method_expression(model, candidates)
    tag_expression = sum(
        len(item.candidate["matched_tags"]) * item.variable
        for item in candidates
    )
    return _PlanningModel(
        model=model,
        candidates=candidates,
        score_expression=score_expression,
        bad_expression=bad_expression,
        composition_expression=composition_expression,
        temperature_expression=temperature_expression,
        method_expression=method_expression,
        selected_count=selected_count,
        tag_expression=tag_expression,
    )


def _resolve_selected_count(
    planning_input: MenuPlanningInput,
    diners: int,
) -> int:
    explicit_total = planning_input["total_dish_count"]
    if explicit_total is not None:
        return explicit_total
    if any(dish["count"] is None for dish in planning_input["dishes"]):
        return diners if diners <= 3 else diners - 1
    return sum(
        dish["count"] or 0 for dish in planning_input["dishes"]
    )


def _add_composition_expression(
    model: cp_model.CpModel,
    candidates: list[CandidateSelection],
    selected_count: int,
) -> Any:
    """先缩小荤素差；差相同时以素菜更多为优。"""

    if selected_count < 2:
        return 0
    meat_count = sum(
        item.variable
        for item in candidates
        if item.candidate["composition_type"] == "荤"
    )
    vegetarian_count = sum(
        item.variable
        for item in candidates
        if item.candidate["composition_type"] == "素"
    )
    difference = model.new_int_var(
        0,
        selected_count,
        "composition_count_difference",
    )
    model.add_abs_equality(difference, meat_count - vegetarian_count)
    return (
        (selected_count - difference) * (selected_count + 1)
        + vegetarian_count
    )


def _add_temperature_expression(
    model: cp_model.CpModel,
    candidates: list[CandidateSelection],
    selected_count: int,
) -> Any:
    """四项起仅评价是否同时有冷菜且热菜数量更多。"""

    if selected_count < 4:
        return 0
    cold_count = sum(
        item.variable
        for item in candidates
        if item.candidate["serving_temperature"] == "冷"
    )
    hot_count = sum(
        item.variable
        for item in candidates
        if item.candidate["serving_temperature"] == "热"
    )
    has_cold = model.new_bool_var("has_cold_dish")
    model.add(cold_count >= 1).only_enforce_if(has_cold)
    model.add(cold_count == 0).only_enforce_if(has_cold.Not())
    has_more_hot = model.new_bool_var("has_more_hot_than_cold")
    model.add(hot_count >= cold_count + 1).only_enforce_if(has_more_hot)
    model.add(hot_count <= cold_count).only_enforce_if(has_more_hot.Not())
    is_preferred = model.new_bool_var("temperature_pairing_preferred")
    model.add(is_preferred <= has_cold)
    model.add(is_preferred <= has_more_hot)
    model.add(is_preferred >= has_cold + has_more_hot - 1)
    return is_preferred


def _add_method_expression(
    model: cp_model.CpModel,
    candidates: list[CandidateSelection],
) -> Any:
    """计算已选菜单中不同主烹饪方式的数量。"""

    used_methods: list[cp_model.IntVar] = []
    for method in PRIMARY_COOKING_METHODS:
        method_count = sum(
            item.variable
            for item in candidates
            if item.candidate["primary_cooking_method"] == method
        )
        is_used = model.new_bool_var(f"method_used_{method}")
        model.add(method_count >= 1).only_enforce_if(is_used)
        model.add(method_count == 0).only_enforce_if(is_used.Not())
        used_methods.append(is_used)
    return sum(used_methods)


def _add_menu_change_constraints(
    model: cp_model.CpModel,
    planning_input: MenuPlanningInput,
    variables_by_name: Mapping[str, list[cp_model.IntVar]],
) -> None:
    """把历史保留数、必选菜和禁选菜编码为硬约束。"""

    policy = planning_input.get("menu_change_policy")
    if policy is None:
        return
    for name in policy["forbidden_recipe_names"]:
        model.add(sum(variables_by_name.get(name, [])) == 0)
    for name in policy["required_recipe_names"]:
        variables = variables_by_name.get(name, [])
        if not variables:
            model.add_bool_or([])
        else:
            model.add(sum(variables) == 1)
    retained_count = policy["required_previous_count"]
    if retained_count is not None:
        previous_variables = [
            variable
            for name in policy["previous_recipe_names"]
            for variable in variables_by_name.get(name, [])
        ]
        model.add(sum(previous_variables) == retained_count)


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

    target_count = planning_input["total_dish_count"]
    if target_count is None and has_unspecified_count:
        target_count = diners if diners <= 3 else diners - 1
    if target_count is not None:
        selected_count = sum(item.variable for item in candidates)
        model.add(selected_count == target_count)


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
    """把固定优先级编码为单一目标，避免按候选逐个重复求解。"""

    model = planning_model.model
    model.maximize(_build_lexicographic_objective(planning_model))
    try:
        result = runner(model, SOLVE_TIMEOUT_SECONDS)
    except Exception as exc:
        raise MenuPlanningError(500, "CP-SAT求解执行失败") from exc
    _require_optimal_status(result)

    return [
        index
        for index, candidate in enumerate(planning_model.candidates)
        if _result_value(result, candidate.variable) == 1
    ]


def _build_lexicographic_objective(planning_model: _PlanningModel) -> Any:
    """按固定优先级和各级上界构造单一整数目标。"""

    candidate_count = len(planning_model.candidates)
    rank_expression = sum(
        (candidate_count - index) * candidate.variable
        for index, candidate in enumerate(planning_model.candidates)
    )
    rank_upper_bound = candidate_count * (candidate_count + 1) // 2
    tag_upper_bound = sum(
        len(candidate.candidate["matched_tags"])
        for candidate in planning_model.candidates
    )
    bad_upper_bound = len(NUTRIENT_FIELDS)
    composition_upper_bound = (
        planning_model.selected_count
        * (planning_model.selected_count + 1)
        + planning_model.selected_count
    )
    method_upper_bound = min(
        len(PRIMARY_COOKING_METHODS),
        planning_model.selected_count,
    )
    combined_objective = planning_model.score_expression
    for expression, upper_bound in (
        (bad_upper_bound - planning_model.bad_expression, bad_upper_bound),
        (planning_model.composition_expression, composition_upper_bound),
        (planning_model.temperature_expression, 1),
        (planning_model.method_expression, method_upper_bound),
        (planning_model.tag_expression, tag_upper_bound),
        (rank_expression, rank_upper_bound),
    ):
        combined_objective = (
            combined_objective * (upper_bound + 1) + expression
        )
    return combined_objective


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
