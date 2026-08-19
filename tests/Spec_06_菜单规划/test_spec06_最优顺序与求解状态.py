from __future__ import annotations

from .spec06_support import (
    build_candidate,
    build_dish,
    build_nutrition,
    build_planning_input,
)


def plan_one_of(candidates, invoke_plan):
    result = invoke_plan(
        build_planning_input(dishes=[build_dish(candidates=candidates)])
    )
    return result["selected_dishes"][0]["recipe_name"]


def test_先选择营养总分更高的菜单(invoke_plan):
    lower_score = build_candidate(
        "标签更多但营养较差",
        matched_tags=["午餐", "粤菜", "清淡"],
        nutrition=build_nutrition(energy_kcal="630"),
    )
    higher_score = build_candidate(
        "营养优秀",
        matched_tags=[],
        nutrition=build_nutrition(),
    )
    assert plan_one_of([lower_score, higher_score], invoke_plan) == "营养优秀"


def test_总分相同时优先bad项更少的菜单(invoke_plan):
    one_bad = build_candidate(
        "一个bad",
        nutrition=build_nutrition(energy_kcal="630"),
    )
    two_normal = build_candidate(
        "两个normal",
        nutrition=build_nutrition(energy_kcal="680", fiber_g="9"),
    )
    assert plan_one_of([one_bad, two_normal], invoke_plan) == "两个normal"


def test_营养完全相同时优先命中标签更多的菜单(invoke_plan):
    fewer_tags = build_candidate("少标签", matched_tags=["午餐"])
    more_tags = build_candidate(
        "多标签", matched_tags=["午餐", "粤菜", "清淡"]
    )
    assert plan_one_of([fewer_tags, more_tags], invoke_plan) == "多标签"


def test_完全同分时保持候选原顺序(invoke_plan):
    first = build_candidate("第一个")
    second = build_candidate("第二个")
    assert plan_one_of([first, second], invoke_plan) == "第一个"


def test_全部优先级合并为一次确定性求解(invoke_plan):
    from backend.services.menu_planning_solver import default_solver_runner

    observed_timeouts = []

    def counting_runner(model, timeout_seconds):
        observed_timeouts.append(timeout_seconds)
        return default_solver_runner(model, timeout_seconds)

    candidates = [
        build_candidate(f"候选{index}")
        for index in range(20)
    ]
    result = invoke_plan(
        build_planning_input(
            dishes=[build_dish(candidates=candidates)],
        ),
        solver_runner=counting_runner,
    )

    assert result["selected_dishes"][0]["recipe_name"] == "候选0"
    assert observed_timeouts == [10]


def test_十秒内未证明最优返回503(assert_plan_error):
    observed_timeout = []

    def unknown_runner(model, timeout_seconds):
        del model
        observed_timeout.append(timeout_seconds)
        return {"status": "UNKNOWN"}

    assert_plan_error(
        build_planning_input(),
        expected_status=503,
        solver_runner=unknown_runner,
    )
    assert observed_timeout == [10]


def test_求解模型非法返回500(assert_plan_error):
    def invalid_runner(model, timeout_seconds):
        del model, timeout_seconds
        return {"status": "MODEL_INVALID"}

    assert_plan_error(
        build_planning_input(),
        expected_status=500,
        solver_runner=invalid_runner,
    )


def test_求解器内部异常返回500(assert_plan_error):
    def failed_runner(model, timeout_seconds):
        del model, timeout_seconds
        raise RuntimeError("求解器失败")

    assert_plan_error(
        build_planning_input(),
        expected_status=500,
        solver_runner=failed_runner,
    )
