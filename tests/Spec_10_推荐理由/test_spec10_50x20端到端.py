from __future__ import annotations

import html
import json
import os
import random
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from backend.core.dish_filtering_contract import TAG_TO_GROUP
from tests.graph_data_support import ensure_graph_data


REPO_ROOT = Path(__file__).resolve().parents[2]
USERS_PATH = (
    REPO_ROOT
    / "datas"
    / "processed"
    / "users"
    / "50个用户健康档案_归一化.json"
)
DIALOGUES_PATH = REPO_ROOT / "datas" / "raw" / "对话用例.json"
RECIPES_PATH = (
    REPO_ROOT
    / "datas"
    / "processed"
    / "Recipes"
    / "RecipeComplete.json"
)
INGREDIENTS_PATH = (
    REPO_ROOT
    / "datas"
    / "processed"
    / "Ingredients"
    / "Ingredients2Nutrition.csv"
)
DRI_PATH = (
    REPO_ROOT / "datas" / "processed" / "Nutrition" / "DRI2023.csv"
)
REPORT_PATH = (
    REPO_ROOT
    / "docs"
    / "spec_10"
    / "Spec_10_50x20端到端业务报告.html"
)
CASES_DATA_PATH = (
    REPO_ROOT / "tests" / ".pytest-tmp" / "spec10_50x20_cases.json"
)
SUPPORTED_MEAL_PERIODS = frozenset({"早餐", "午餐", "晚餐"})
CANDIDATE_LIMIT_PER_DISH = 100
CANDIDATE_RANDOM_SEED = 42
EXPECTED_PROFILE_COUNT = 50
EXPECTED_DIALOGUE_COUNT = 20
TAG_GROUP_ORDER = ("餐次", "口味", "菜系", "功效", "人群")
NUTRIENT_ORDER = (
    "energy_kcal",
    "protein_g",
    "fat_g",
    "carbohydrate_g",
    "fiber_g",
    "sodium_mg",
    "calcium_mg",
    "iron_mg",
)
LLM_ENVIRONMENT_NAMES = frozenset(
    {"LLM_PROVIDER", "LLM_BASE_URL", "LLM_AUTH_TOKEN", "LLM_MODEL"}
)


@dataclass
class CaseResult:
    """一组档案与对话贯通到推荐理由后的业务结果。"""

    profile_id: int
    dialogue_id: int
    status: str
    meal_period: str
    diner_count: int | None
    special_populations: list[str]
    allergens: list[str]
    selected_recipes: list[str]
    dish_reason_counts: list[int]
    tag_groups: list[str]
    health_constraints: list[str]
    nutrition_score: int | None
    total_reason_count: int
    elapsed_seconds: float
    detail: str
    recommendation: dict[str, Any] | None


class DialogueExtractionAttemptError(RuntimeError):
    """携带一次真实提取实际发起的LLM调用数。"""

    def __init__(self, llm_calls: int, cause: Exception) -> None:
        super().__init__(f"{type(cause).__name__}：{cause}")
        self.llm_calls = llm_calls


class DialogueExtractionError(RuntimeError):
    """两次真实提取均失败，并保留累计调用信息。"""

    def __init__(
        self,
        llm_calls: int,
        attempts: int,
        errors: list[str],
    ) -> None:
        super().__init__("；".join(errors))
        self.llm_calls = llm_calls
        self.attempts = attempts


def _fixed_clock() -> datetime:
    """未明确餐次时固定按上海午餐窗口解析。"""

    return datetime(2026, 8, 19, 12, 0)


def _load_dotenv(env_path: Path | None = None) -> None:
    """加载环境；LLM配置以.env为准，其他配置保留进程优先级。"""

    env_path = env_path or REPO_ROOT / ".env"
    if not env_path.exists():
        raise AssertionError("真实端到端测试需要仓库根目录下的.env")
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        normalized_name = name.strip()
        normalized_value = value.strip()
        if normalized_name in LLM_ENVIRONMENT_NAMES:
            os.environ[normalized_name] = normalized_value
        else:
            os.environ.setdefault(normalized_name, normalized_value)


def _load_json_array(path: Path) -> list[dict[str, Any]]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, list), f"{path.name}顶层必须是数组"
    assert all(isinstance(item, dict) for item in loaded), (
        f"{path.name}只能包含对象"
    )
    return loaded


def _load_test_database_url() -> str:
    """只允许返回项目明确命名的PostgreSQL测试库。"""

    import tomllib

    from sqlalchemy.engine import make_url

    with (REPO_ROOT / "pyproject.toml").open("rb") as stream:
        project_config = tomllib.load(stream)
    test_config = project_config["tool"]["mealagent"]["test_database"]
    database_url = test_config["url"]
    required_database = test_config["required_database"]
    parsed_url = make_url(database_url)
    if (
        not parsed_url.drivername.startswith("postgresql")
        or parsed_url.database != required_database
    ):
        raise pytest.UsageError(
            f"多轮会话测试只允许连接{required_database}"
        )
    return database_url


@contextmanager
def _create_multi_turn_service() -> Any:
    """在隔离测试库初始化多轮会话所需数据和真实提取器。"""

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from backend.infrastructure.database import create_session_factory
    from backend.infrastructure.database.importer import import_basic_data
    from backend.infrastructure.database.models import Base
    from backend.infrastructure.llm import (
        create_langchain_multi_turn_extractor_from_environment,
    )
    from backend.services import MultiTurnConstraintService
    from backend.services.meal_period_resolution import (
        MealPeriodResolutionService,
    )

    engine = create_engine(_load_test_database_url(), pool_pre_ping=True)
    try:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            import_basic_data(
                RECIPES_PATH,
                INGREDIENTS_PATH,
                USERS_PATH,
                DRI_PATH,
                session,
            )
        session_factory = create_session_factory(engine)
        yield MultiTurnConstraintService(
            session_factory,
            create_langchain_multi_turn_extractor_from_environment(),
            MealPeriodResolutionService(
                clock=_fixed_clock,
                timezone_name="Asia/Shanghai",
            ),
        )
    finally:
        engine.dispose()


def _extract_dialogue_constraints(
    dialogue: dict[str, Any],
    *,
    profile_id: int,
    single_turn_service: Any,
    multi_turn_service: Any,
) -> tuple[dict[str, Any], int, int]:
    """按用例轮数提取；外部模型失败时从新会话重试一次。"""

    total_llm_calls = 0
    errors: list[str] = []
    for attempt in range(1, 3):
        try:
            constraints, llm_calls = _extract_dialogue_constraints_once(
                dialogue,
                profile_id=profile_id,
                single_turn_service=single_turn_service,
                multi_turn_service=multi_turn_service,
            )
            return constraints, total_llm_calls + llm_calls, attempt
        except DialogueExtractionAttemptError as exc:
            total_llm_calls += exc.llm_calls
            errors.append(f"第{attempt}次：{exc}")
    raise DialogueExtractionError(total_llm_calls, 2, errors)


def _extract_dialogue_constraints_once(
    dialogue: dict[str, Any],
    *,
    profile_id: int,
    single_turn_service: Any,
    multi_turn_service: Any,
) -> tuple[dict[str, Any], int]:
    """执行一轮完整的单轮或多轮提取尝试。"""

    messages = dialogue["user_messages"]
    if dialogue["turn_count"] == 1:
        try:
            return single_turn_service.extract(dialogue), 1
        except Exception as exc:
            raise DialogueExtractionAttemptError(1, exc) from exc

    llm_calls = 0
    try:
        session_id = multi_turn_service.create_session(profile_id)
        result: dict[str, Any] | None = None
        for message in messages:
            llm_calls += 1
            result = multi_turn_service.submit_turn(session_id, message)
        assert result is not None
        return result["merged_constraints"], llm_calls
    except Exception as exc:
        raise DialogueExtractionAttemptError(llm_calls, exc) from exc


def _sample_candidate_group(
    candidates: list[dict[str, Any]],
    *,
    profile_id: int,
    dialogue_id: int,
    dish_index: int,
) -> list[dict[str, Any]]:
    """按稳定种子限制求解规模，并恢复候选原始顺序。"""

    if len(candidates) <= CANDIDATE_LIMIT_PER_DISH:
        return list(candidates)
    seed = (
        CANDIDATE_RANDOM_SEED * 1_000_000
        + profile_id * 10_000
        + dialogue_id * 100
        + dish_index
    )
    indexes = sorted(
        random.Random(seed).sample(
            range(len(candidates)), CANDIDATE_LIMIT_PER_DISH
        )
    )
    return [candidates[index] for index in indexes]


def _build_menu_input(
    *,
    profile_constraints: dict[str, Any],
    integrated: dict[str, Any],
    filtering_result: dict[str, Any],
    nutrition_service: Any,
    meal_period: str,
) -> tuple[dict[str, Any], list[int]]:
    """把真实筛选候选和营养数据组装为菜单规划输入。"""

    candidate_counts = [
        len(candidates) for candidates in filtering_result["dishes"]
    ]
    limited_groups = [
        _sample_candidate_group(
            candidates,
            profile_id=integrated["profile_id"],
            dialogue_id=integrated["dialogue_id"],
            dish_index=dish_index,
        )
        for dish_index, candidates in enumerate(filtering_result["dishes"])
    ]
    recipe_names = list(
        dict.fromkeys(
            candidate["recipe_name"]
            for candidates in limited_groups
            for candidate in candidates
        )
    )
    nutrition_by_name: dict[str, dict[str, Any]] = {}
    if recipe_names:
        nutrition_by_name = {
            item["recipe_name"]: item
            for item in nutrition_service.get_recipe_nutrition(recipe_names)
        }

    dishes = []
    for dish, candidates in zip(
        integrated["dishes"], limited_groups, strict=True
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
                        for field in (
                            *NUTRIENT_ORDER,
                            "cholesterol_mg",
                        )
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

    targets = nutrition_service.get_meal_nutrition_targets(
        integrated["profile_id"], meal_period
    )
    return (
        {
            "profile_id": integrated["profile_id"],
            "dialogue_id": integrated["dialogue_id"],
            "meal_period": meal_period,
            "diner_count": integrated["diner_count"],
            "total_dish_count": integrated["total_dish_count"],
            "special_populations": list(
                profile_constraints["special_populations"]
            ),
            "dishes": dishes,
            "nutrient_targets": targets["nutrients"],
            "unmatched_allergens": list(
                filtering_result["unmatched_allergens"]
            ),
        },
        candidate_counts,
    )


def _assert_recommendation_result(
    recommendation: dict[str, Any],
    filtering_result: dict[str, Any],
    planning_result: dict[str, Any],
) -> None:
    """检查推荐理由与两份真实上游结果之间的跨模块不变量。"""

    assert recommendation["profile_id"] == planning_result["profile_id"]
    assert recommendation["dialogue_id"] == planning_result["dialogue_id"]
    selected = planning_result["selected_dishes"]
    dish_recommendations = recommendation["dish_recommendations"]
    assert [
        (item["dish_constraint_index"], item["recipe_name"])
        for item in dish_recommendations
    ] == [
        (item["dish_constraint_index"], item["recipe_name"])
        for item in selected
    ]

    for selected_index, (selected_dish, dish_reason) in enumerate(
        zip(selected, dish_recommendations, strict=True)
    ):
        dish_index = selected_dish["dish_constraint_index"]
        candidates = filtering_result["dishes"][dish_index]
        matches = [
            (index, candidate)
            for index, candidate in enumerate(candidates)
            if candidate["recipe_name"] == selected_dish["recipe_name"]
        ]
        assert len(matches) == 1
        candidate_index, candidate = matches[0]
        reasons = dish_reason["reasons"]
        assert [reason["matched_group"] for reason in reasons] == [
            group
            for group in TAG_GROUP_ORDER
            if group in candidate["matched_groups"]
        ]
        assert [tag for reason in reasons for tag in reason["matched_tags"]] == [
            tag
            for group in TAG_GROUP_ORDER
            for tag in candidate["matched_tags"]
            if TAG_TO_GROUP[tag] == group
        ]
        for reason in reasons:
            assert reason["text"]
            assert reason["sources"] == [
                {
                    "component": "menu_planning",
                    "paths": [
                        f"selected_dishes[{selected_index}].dish_constraint_index",
                        f"selected_dishes[{selected_index}].recipe_name",
                    ],
                },
                {
                    "component": "dish_filtering",
                    "paths": [
                        f"dishes[{dish_index}][{candidate_index}].matched_tags",
                        f"dishes[{dish_index}][{candidate_index}].matched_groups",
                    ],
                },
            ]

    menu_reasons = recommendation["menu_reasons"]
    expected_health = planning_result["applied_health_constraints"]
    assert [
        reason["constraint"]
        for reason in menu_reasons[:-1]
    ] == expected_health
    assert all(
        reason["reason_type"] == "health_constraint"
        for reason in menu_reasons[:-1]
    )
    nutrition = menu_reasons[-1]
    assert nutrition["reason_type"] == "nutrition_summary"
    assert nutrition["nutrition_score"] == planning_result["nutrition_score"]
    assert nutrition["max_score"] == 16
    details = nutrition["nutrient_details"]
    assert [detail["nutrient"] for detail in details] == list(NUTRIENT_ORDER)
    assert sum(detail["score"] for detail in details) == (
        planning_result["nutrition_score"]
    )
    assert [detail["menu_total_value"] for detail in details] == [
        planning_result["nutrient_grades"][nutrient]["actual_value"]
        for nutrient in NUTRIENT_ORDER
    ]


def _new_case_result(
    *,
    profile_id: int,
    dialogue_id: int,
    status: str,
    profile_constraints: dict[str, Any] | None,
    integrated: dict[str, Any] | None,
    started_at: float,
    detail: str,
    meal_period: str = "",
    planning_result: dict[str, Any] | None = None,
    recommendation: dict[str, Any] | None = None,
) -> CaseResult:
    dish_recommendations = (recommendation or {}).get(
        "dish_recommendations", []
    )
    menu_reasons = (recommendation or {}).get("menu_reasons", [])
    tag_groups = [
        reason["matched_group"]
        for dish in dish_recommendations
        for reason in dish["reasons"]
    ]
    health_constraints = [
        reason["constraint"]
        for reason in menu_reasons
        if reason["reason_type"] == "health_constraint"
    ]
    return CaseResult(
        profile_id=profile_id,
        dialogue_id=dialogue_id,
        status=status,
        meal_period=meal_period,
        diner_count=(integrated or {}).get("diner_count"),
        special_populations=list(
            (profile_constraints or {}).get("special_populations", [])
        ),
        allergens=list((profile_constraints or {}).get("allergens", [])),
        selected_recipes=[
            item["recipe_name"]
            for item in (planning_result or {}).get("selected_dishes", [])
        ],
        dish_reason_counts=[
            len(item["reasons"]) for item in dish_recommendations
        ],
        tag_groups=tag_groups,
        health_constraints=health_constraints,
        nutrition_score=(planning_result or {}).get("nutrition_score"),
        total_reason_count=sum(
            len(item["reasons"]) for item in dish_recommendations
        )
        + len(menu_reasons),
        elapsed_seconds=round(time.perf_counter() - started_at, 4),
        detail=detail,
        recommendation=recommendation,
    )


def _run_case(
    *,
    profile_id: int,
    dialogue_id: int,
    profile_constraints: dict[str, Any] | None,
    dialogue_constraints: dict[str, Any] | None,
    profile_error: str | None,
    dialogue_error: str | None,
    meal_period_service: Any,
    services: Any,
    integration_service: Any,
    nutrition_service: Any,
    menu_service: Any,
    reason_service: Any,
    menu_error_type: type[Exception],
) -> CaseResult:
    """运行完整链路，并区分正常业务门禁与技术失败。"""

    started_at = time.perf_counter()
    if profile_error is not None or dialogue_error is not None:
        detail = profile_error or dialogue_error or "上游提取失败"
        return _new_case_result(
            profile_id=profile_id,
            dialogue_id=dialogue_id,
            status="technical_failure",
            profile_constraints=profile_constraints,
            integrated=None,
            started_at=started_at,
            detail=detail,
        )

    integrated: dict[str, Any] | None = None
    meal_period = ""
    try:
        assert profile_constraints is not None
        assert dialogue_constraints is not None
        integrated = integration_service.integrate(
            profile_constraints, dialogue_constraints
        )
        if integrated["has_conflicts"]:
            return _new_case_result(
                profile_id=profile_id,
                dialogue_id=dialogue_id,
                status="constraint_conflict",
                profile_constraints=profile_constraints,
                integrated=integrated,
                started_at=started_at,
                detail=json.dumps(
                    integrated["conflicts"], ensure_ascii=False
                ),
            )

        meal_periods = integrated["meal_periods"]
        if (
            len(meal_periods) == 1
            and meal_periods[0] in SUPPORTED_MEAL_PERIODS
        ):
            meal_period = meal_periods[0]
        else:
            resolution = meal_period_service.resolve(meal_periods)
            if resolution["status"] == "needs_confirmation":
                return _new_case_result(
                    profile_id=profile_id,
                    dialogue_id=dialogue_id,
                    status="meal_period_blocked",
                    profile_constraints=profile_constraints,
                    integrated=integrated,
                    started_at=started_at,
                    detail=resolution["reason"],
                )
            meal_period = resolution["meal_period"]

        filtering_result = services.dish_filtering.filter(integrated)
        planning_input, candidate_counts = _build_menu_input(
            profile_constraints=profile_constraints,
            integrated=integrated,
            filtering_result=filtering_result,
            nutrition_service=nutrition_service,
            meal_period=meal_period,
        )
        try:
            planning_result = menu_service.plan(planning_input)
        except menu_error_type as exc:
            if getattr(exc, "status_code", None) != 422:
                raise
            if filtering_result["unmatched_allergens"]:
                status = "allergen_blocked"
            elif any(count == 0 for count in candidate_counts):
                status = "empty_candidate_blocked"
            else:
                status = "planning_infeasible"
            return _new_case_result(
                profile_id=profile_id,
                dialogue_id=dialogue_id,
                status=status,
                profile_constraints=profile_constraints,
                integrated=integrated,
                started_at=started_at,
                detail=str(exc),
                meal_period=meal_period,
            )

        try:
            recommendation = reason_service.build(
                filtering_result, planning_result
            )
            _assert_recommendation_result(
                recommendation, filtering_result, planning_result
            )
            assert recommendation == reason_service.build(
                filtering_result, planning_result
            )
        except Exception as exc:
            return _new_case_result(
                profile_id=profile_id,
                dialogue_id=dialogue_id,
                status="reason_failure",
                profile_constraints=profile_constraints,
                integrated=integrated,
                started_at=started_at,
                detail=f"{type(exc).__name__}：{exc}",
                meal_period=meal_period,
                planning_result=planning_result,
            )
        return _new_case_result(
            profile_id=profile_id,
            dialogue_id=dialogue_id,
            status="recommended",
            profile_constraints=profile_constraints,
            integrated=integrated,
            started_at=started_at,
            detail="菜单规划与推荐理由均成功",
            meal_period=meal_period,
            planning_result=planning_result,
            recommendation=recommendation,
        )
    except Exception as exc:
        return _new_case_result(
            profile_id=profile_id,
            dialogue_id=dialogue_id,
            status="technical_failure",
            profile_constraints=profile_constraints,
            integrated=integrated,
            started_at=started_at,
            detail=f"{type(exc).__name__}：{exc}",
            meal_period=meal_period,
        )


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _status_label(status: str) -> str:
    return {
        "recommended": "推荐理由成功",
        "constraint_conflict": "约束冲突",
        "meal_period_blocked": "餐次待确认",
        "allergen_blocked": "过敏安全门禁",
        "empty_candidate_blocked": "存在空候选",
        "planning_infeasible": "规划硬约束无解",
        "reason_failure": "推荐理由失败",
        "technical_failure": "技术失败",
    }.get(status, status)


def _status_class(status: str) -> str:
    if status == "recommended":
        return "ok"
    if status in {
        "constraint_conflict",
        "meal_period_blocked",
        "allergen_blocked",
        "empty_candidate_blocked",
        "planning_infeasible",
    }:
        return "blocked"
    return "fail"


def _generate_report(
    *,
    users: list[dict[str, Any]],
    dialogues: list[dict[str, Any]],
    cases: list[CaseResult],
    dialogue_constraints: dict[int, dict[str, Any]],
    dialogue_timings: dict[int, float],
    dialogue_llm_calls: dict[int, int],
    dialogue_attempts: dict[int, int],
    environment: dict[str, Any],
    total_elapsed: float,
) -> None:
    """生成无需外部资源即可审阅的HTML业务报告。"""

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    counts = Counter(case.status for case in cases)
    by_dialogue: dict[int, list[CaseResult]] = defaultdict(list)
    by_profile: dict[int, list[CaseResult]] = defaultdict(list)
    for case in cases:
        by_dialogue[case.dialogue_id].append(case)
        by_profile[case.profile_id].append(case)

    tag_counts = Counter(
        group for case in cases for group in case.tag_groups
    )
    health_counts = Counter(
        constraint
        for case in cases
        for constraint in case.health_constraints
    )
    score_counts = Counter(
        case.nutrition_score
        for case in cases
        if case.nutrition_score is not None
    )
    recommended_cases = [
        case for case in cases if case.status == "recommended"
    ]
    selected_dish_count = sum(
        len(case.selected_recipes) for case in recommended_cases
    )
    dish_reason_count = sum(
        sum(case.dish_reason_counts) for case in recommended_cases
    )
    zero_tag_dishes = sum(
        count == 0
        for case in recommended_cases
        for count in case.dish_reason_counts
    )

    dialogue_rows = []
    dialogue_by_id = {item["id"]: item for item in dialogues}
    for dialogue_id in sorted(by_dialogue):
        rows = by_dialogue[dialogue_id]
        status_counts = Counter(row.status for row in rows)
        extracted = dialogue_constraints.get(dialogue_id, {})
        dialogue_rows.append(
            "<tr>"
            f"<td>{dialogue_id}</td>"
            f"<td>{_escape(dialogue_by_id[dialogue_id]['user_messages'][0])}</td>"
            f"<td>{dialogue_by_id[dialogue_id]['turn_count']}</td>"
            f"<td>{_escape(extracted.get('meal_periods', '提取失败'))}</td>"
            f"<td>{_escape(extracted.get('diner_count', ''))}</td>"
            f"<td>{dialogue_llm_calls.get(dialogue_id, 0)}</td>"
            f"<td>{dialogue_attempts.get(dialogue_id, 0)}</td>"
            f"<td>{dialogue_timings.get(dialogue_id, 0):.3f}s</td>"
            f"<td>{status_counts.get('recommended', 0)}</td>"
            f"<td>{_escape(', '.join(f'{_status_label(k)}={v}' for k, v in status_counts.items() if k != 'recommended') or '-')}</td>"
            "</tr>"
        )

    user_by_id = {user["id"]: user for user in users}
    profile_rows = []
    for profile_id in sorted(by_profile):
        user = user_by_id[profile_id]
        rows = by_profile[profile_id]
        status_counts = Counter(row.status for row in rows)
        profile_rows.append(
            "<tr>"
            f"<td>{profile_id}</td>"
            f"<td>{_escape(user.get('性别', '-'))} / {_escape(user.get('年龄', '-'))}</td>"
            f"<td>{_escape(rows[0].special_populations)}</td>"
            f"<td>{status_counts.get('recommended', 0)}</td>"
            f"<td>{_escape(', '.join(f'{_status_label(k)}={v}' for k, v in status_counts.items() if k != 'recommended') or '-')}</td>"
            f"<td>{sum(row.elapsed_seconds for row in rows) / len(rows):.3f}s</td>"
            "</tr>"
        )

    case_rows = []
    detail_blocks = []
    for case in cases:
        case_rows.append(
            "<tr>"
            f"<td>{case.profile_id}</td>"
            f"<td>{case.dialogue_id}</td>"
            f'<td><span class="status {_status_class(case.status)}">{_escape(_status_label(case.status))}</span></td>'
            f"<td>{_escape(case.meal_period or '-')}</td>"
            f"<td>{_escape(case.selected_recipes or '-')}</td>"
            f"<td>{_escape(case.dish_reason_counts or '-')}</td>"
            f"<td>{_escape(case.health_constraints or '-')}</td>"
            f"<td>{_escape(case.nutrition_score if case.nutrition_score is not None else '-')}</td>"
            f"<td>{case.total_reason_count}</td>"
            f"<td>{case.elapsed_seconds:.4f}s</td>"
            f"<td>{_escape(case.detail)}</td>"
            "</tr>"
        )
        if case.recommendation is not None:
            detail_blocks.append(
                "<details>"
                f"<summary>档案{case.profile_id} × 对话{case.dialogue_id}："
                f"{_escape(case.selected_recipes)}；营养{case.nutrition_score}/16</summary>"
                "<pre>"
                + _escape(
                    json.dumps(
                        case.recommendation,
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    )
                )
                + "</pre></details>"
            )

    score_rows = "".join(
        f"<tr><td>{score}</td><td>{score_counts[score]}</td></tr>"
        for score in sorted(score_counts)
    )
    generation_time = datetime.now().astimezone().isoformat(timespec="seconds")
    pass_status = (
        "通过"
        if counts.get("technical_failure", 0) == 0
        and counts.get("reason_failure", 0) == 0
        and recommended_cases
        else "未通过"
    )
    pass_class = "ok" if pass_status == "通过" else "fail"

    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Spec_10 50×20 端到端业务报告</title>
  <style>
    :root {{ color-scheme:light; --ink:#17202a; --muted:#65717e; --line:#dce3ea; --brand:#155eef; --ok:#087a55; --warn:#9a6700; --fail:#c62828; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font:14px/1.55 "Segoe UI","Microsoft YaHei",sans-serif; color:var(--ink); background:#f5f7fa; }}
    header {{ padding:36px max(24px,5vw); color:white; background:linear-gradient(120deg,#0c3175,#155eef); }}
    header h1 {{ margin:0 0 8px; font-size:30px; }}
    header p {{ margin:4px 0; opacity:.9; }}
    main {{ max-width:1600px; margin:auto; padding:24px; }}
    section {{ margin:0 0 24px; padding:22px; background:white; border:1px solid var(--line); border-radius:12px; box-shadow:0 4px 16px #23395d0c; }}
    h2 {{ margin-top:0; font-size:21px; }}
    .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; }}
    .card {{ padding:14px; border:1px solid var(--line); border-radius:10px; background:#fbfcfe; }}
    .card span {{ display:block; color:var(--muted); }}
    .card strong {{ display:block; margin-top:4px; font-size:25px; }}
    .note {{ padding:12px 14px; border-left:4px solid var(--brand); background:#eef4ff; }}
    .table-wrap {{ overflow:auto; max-height:720px; border:1px solid var(--line); border-radius:8px; }}
    table {{ width:100%; border-collapse:collapse; white-space:nowrap; }}
    th,td {{ padding:9px 10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }}
    th {{ position:sticky; top:0; z-index:1; background:#edf2f8; }}
    tr:hover td {{ background:#f8fbff; }}
    .status {{ display:inline-block; padding:2px 8px; border-radius:99px; font-weight:600; }}
    .status.ok {{ color:var(--ok); background:#e8f7f1; }}
    .status.blocked {{ color:var(--warn); background:#fff4d6; }}
    .status.fail {{ color:var(--fail); background:#ffebee; }}
    code {{ padding:2px 5px; background:#eef1f5; border-radius:4px; }}
    pre {{ overflow:auto; padding:12px; border:1px solid var(--line); border-radius:8px; background:#f7f9fb; white-space:pre-wrap; }}
    details {{ margin:8px 0; }}
    summary {{ cursor:pointer; font-weight:600; }}
    .muted {{ color:var(--muted); }}
  </style>
</head>
<body>
<header>
  <h1>Spec_10 推荐理由：50份档案 × 20组完整对话</h1>
  <p>真实 PostgreSQL + 真实 LLM + 真实 Neo4j + CP-SAT + 固定模板推荐理由</p>
  <p>生成时间：{generation_time}；总耗时：{total_elapsed:.3f} 秒</p>
</header>
<main>
  <section>
    <h2>执行口径</h2>
    <p class="note">每组输入依次经过档案约束提取、单轮对话约束提取、约束整合、知识图谱菜品筛选、营养查询、CP-SAT菜单规划和推荐理由组装。未明确餐次统一使用固定上海时间12:00解析。候选超过100道时按档案、对话和菜品组构造稳定种子抽样，菜单选中后仍回到完整筛选结果中追溯证据。</p>
    <ul>
      <li>组合：{len(users)}份档案 × {len(dialogues)}组完整对话 = {len(cases)}组。</li>
      <li>数据：{environment['profiles']}份数据库档案、{environment['postgres_recipes']}道PostgreSQL菜谱、{environment['recipe_nutrition']}份营养数据、{environment['neo4j_recipes']}个Neo4j菜谱节点。</li>
      <li>对话：14组单轮 + 6组多轮，共{sum(item['turn_count'] for item in dialogues)}轮用户输入；每组提取一次后供50份档案复用。</li>
      <li>LLM：{_escape(environment['llm_provider'])} / {_escape(environment['llm_model'])}；实际调用{sum(dialogue_llm_calls.values())}次，失败时从新会话完整重试一次。</li>
      <li>推荐理由校验：菜品顺序与组内唯一回溯、标签及来源路径、健康约束顺序、8项营养顺序/数值/总分、重复调用确定性。</li>
      <li>正常业务门禁单独统计，不视为推荐理由技术失败。</li>
    </ul>
  </section>
  <section>
    <h2>结果总览</h2>
    <div class="cards">
      <div class="card"><span>验收结论</span><strong><span class="status {pass_class}">{pass_status}</span></strong></div>
      <div class="card"><span>总组合</span><strong>{len(cases)}</strong></div>
      <div class="card"><span>推荐理由成功</span><strong>{counts.get('recommended', 0)}</strong></div>
      <div class="card"><span>推荐理由失败</span><strong>{counts.get('reason_failure', 0)}</strong></div>
      <div class="card"><span>技术失败</span><strong>{counts.get('technical_failure', 0)}</strong></div>
      <div class="card"><span>业务门禁</span><strong>{len(cases) - counts.get('recommended', 0) - counts.get('reason_failure', 0) - counts.get('technical_failure', 0)}</strong></div>
      <div class="card"><span>入选菜品</span><strong>{selected_dish_count}</strong></div>
      <div class="card"><span>逐菜标签理由</span><strong>{dish_reason_count}</strong></div>
      <div class="card"><span>无标签理由菜品</span><strong>{zero_tag_dishes}</strong></div>
      <div class="card"><span>健康约束理由</span><strong>{sum(health_counts.values())}</strong></div>
      <div class="card"><span>营养摘要理由</span><strong>{len(recommended_cases)}</strong></div>
    </div>
  </section>
  <section>
    <h2>理由覆盖</h2>
    <div class="cards">
      {''.join(f'<div class="card"><span>{group}标签理由</span><strong>{tag_counts.get(group, 0)}</strong></div>' for group in TAG_GROUP_ORDER)}
      <div class="card"><span>高血压约束理由</span><strong>{health_counts.get('高血压', 0)}</strong></div>
      <div class="card"><span>高血糖约束理由</span><strong>{health_counts.get('高血糖', 0)}</strong></div>
    </div>
    <h3>营养得分分布</h3>
    <div class="table-wrap"><table><thead><tr><th>得分（满分16）</th><th>菜单数</th></tr></thead><tbody>{score_rows}</tbody></table></div>
  </section>
  <section>
    <h2>按对话汇总</h2>
    <div class="table-wrap"><table><thead><tr><th>ID</th><th>首句原文</th><th>轮数</th><th>提取餐次</th><th>人数</th><th>LLM调用</th><th>提取尝试</th><th>LLM耗时</th><th>推荐成功</th><th>其他终态</th></tr></thead><tbody>{''.join(dialogue_rows)}</tbody></table></div>
  </section>
  <section>
    <h2>按用户档案汇总</h2>
    <div class="table-wrap"><table><thead><tr><th>档案ID</th><th>性别 / 年龄</th><th>特殊人群</th><th>推荐成功</th><th>其他终态</th><th>平均耗时</th></tr></thead><tbody>{''.join(profile_rows)}</tbody></table></div>
  </section>
  <section>
    <h2>{len(cases)}组端到端明细</h2>
    <div class="table-wrap"><table><thead><tr><th>档案</th><th>对话</th><th>状态</th><th>餐次</th><th>入选菜</th><th>逐菜理由数</th><th>健康理由</th><th>营养分</th><th>理由总数</th><th>耗时</th><th>详情</th></tr></thead><tbody>{''.join(case_rows)}</tbody></table></div>
  </section>
  <section>
    <h2>成功案例结构化输出</h2>
    <p class="muted">展开后可审计每道菜的标签文案、两段来源路径、健康约束及8项营养明细。</p>
    {''.join(detail_blocks)}
  </section>
  <section>
    <h2>验收结论</h2>
    <ul>
      <li>推荐理由成功：{counts.get('recommended', 0)}；推荐理由失败：{counts.get('reason_failure', 0)}；技术失败：{counts.get('technical_failure', 0)}。</li>
      <li>所有成功菜单均完成组内菜名唯一回溯，且最终菜品顺序与推荐理由顺序一致。</li>
      <li>所有标签理由均可同时追溯到菜单规划的最终选择字段和菜品筛选的标签字段。</li>
      <li>所有营养摘要均保留8项固定顺序明细，分项合计与菜单规划总分一致。</li>
      <li>相同真实输入重复组装结果一致。</li>
    </ul>
  </section>
</main>
</body>
</html>
"""
    REPORT_PATH.write_text(document, encoding="utf-8")


@pytest.mark.integration
def test_50份真实档案与20组完整对话贯通到推荐理由() -> None:
    """运行1000种组合，验证单轮和多轮链路可稳定组装推荐理由。"""

    _load_dotenv()
    ensure_graph_data()

    from sqlalchemy import func, select

    from backend.application import create_constraint_services
    from backend.infrastructure.database.models import (
        Recipe,
        RecipeNutrition,
        UserProfile,
    )
    from backend.services import (
        ConstraintIntegrationService,
        MenuPlanningError,
        MenuPlanningService,
        NutritionService,
        RecommendationReasonService,
    )
    from backend.services.meal_period_resolution import (
        MealPeriodResolutionService,
    )

    users = _load_json_array(USERS_PATH)
    dialogues = _load_json_array(DIALOGUES_PATH)
    assert len(users) == EXPECTED_PROFILE_COUNT
    assert len(dialogues) == EXPECTED_DIALOGUE_COUNT

    started_at = time.perf_counter()
    cases: list[CaseResult] = []
    profile_constraints: dict[int, dict[str, Any]] = {}
    profile_errors: dict[int, str] = {}
    dialogue_constraints: dict[int, dict[str, Any]] = {}
    dialogue_errors: dict[int, str] = {}
    dialogue_timings: dict[int, float] = {}
    dialogue_llm_calls: dict[int, int] = {}
    dialogue_attempts: dict[int, int] = {}

    with (
        _create_multi_turn_service() as multi_turn_service,
        create_constraint_services() as services,
    ):
        session_factory = services.profile._session_factory
        nutrition_service = NutritionService(session_factory)
        integration_service = ConstraintIntegrationService()
        menu_service = MenuPlanningService()
        reason_service = RecommendationReasonService()
        meal_period_service = MealPeriodResolutionService(
            clock=_fixed_clock,
            timezone_name="Asia/Shanghai",
        )

        with session_factory() as session:
            postgres_counts = {
                "profiles": session.scalar(select(func.count(UserProfile.id))),
                "postgres_recipes": session.scalar(
                    select(func.count(Recipe.id))
                ),
                "recipe_nutrition": session.scalar(
                    select(func.count(RecipeNutrition.recipe_id))
                ),
            }
        with services._neo4j_driver.session() as graph_session:
            neo4j_recipes = graph_session.run(
                "MATCH (r:Recipe) RETURN count(r) AS value"
            ).single()["value"]

        assert postgres_counts == {
            "profiles": 50,
            "postgres_recipes": 1912,
            "recipe_nutrition": 1912,
        }
        assert neo4j_recipes >= 1900

        for user in users:
            profile_id = user["id"]
            try:
                profile_constraints[profile_id] = services.profile.extract(
                    profile_id
                )
            except Exception as exc:
                profile_errors[profile_id] = f"{type(exc).__name__}：{exc}"

        for dialogue in dialogues:
            dialogue_id = dialogue["id"]
            dialogue_started_at = time.perf_counter()
            try:
                extracted, llm_calls, attempts = (
                    _extract_dialogue_constraints(
                        dialogue,
                        profile_id=users[0]["id"],
                        single_turn_service=services.dialogue,
                        multi_turn_service=multi_turn_service,
                    )
                )
                dialogue_constraints[dialogue_id] = extracted
                dialogue_llm_calls[dialogue_id] = llm_calls
                dialogue_attempts[dialogue_id] = attempts
            except DialogueExtractionError as exc:
                dialogue_llm_calls[dialogue_id] = exc.llm_calls
                dialogue_attempts[dialogue_id] = exc.attempts
                dialogue_errors[dialogue_id] = (
                    f"{type(exc).__name__}：{exc}"
                )
            except Exception as exc:
                dialogue_errors[dialogue_id] = (
                    f"{type(exc).__name__}：{exc}"
                )
            dialogue_timings[dialogue_id] = round(
                time.perf_counter() - dialogue_started_at, 3
            )

        tasks = [
            {
                "profile_id": user["id"],
                "dialogue_id": dialogue["id"],
                "profile_constraints": profile_constraints.get(user["id"]),
                "dialogue_constraints": dialogue_constraints.get(
                    dialogue["id"]
                ),
                "profile_error": profile_errors.get(user["id"]),
                "dialogue_error": dialogue_errors.get(dialogue["id"]),
                "meal_period_service": meal_period_service,
                "services": services,
                "integration_service": integration_service,
                "nutrition_service": nutrition_service,
                "menu_service": menu_service,
                "reason_service": reason_service,
                "menu_error_type": MenuPlanningError,
            }
            for user in users
            for dialogue in dialogues
        ]
        with ThreadPoolExecutor(max_workers=8) as pool:
            cases = list(pool.map(lambda task: _run_case(**task), tasks))

    total_elapsed = time.perf_counter() - started_at
    environment = {
        **postgres_counts,
        "neo4j_recipes": neo4j_recipes,
        "llm_provider": os.environ.get("LLM_PROVIDER", "未配置"),
        "llm_model": os.environ.get("LLM_MODEL", "未配置"),
    }
    report_data = {
        "users": users,
        "dialogues": dialogues,
        "dialogue_constraints": dialogue_constraints,
        "dialogue_timings": dialogue_timings,
        "dialogue_llm_calls": dialogue_llm_calls,
        "dialogue_attempts": dialogue_attempts,
        "cases": [asdict(case) for case in cases],
        "environment": environment,
        "total_elapsed": total_elapsed,
    }
    CASES_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    CASES_DATA_PATH.write_text(
        json.dumps(report_data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    _generate_report(
        users=users,
        dialogues=dialogues,
        cases=cases,
        dialogue_constraints=dialogue_constraints,
        dialogue_timings=dialogue_timings,
        dialogue_llm_calls=dialogue_llm_calls,
        dialogue_attempts=dialogue_attempts,
        environment=environment,
        total_elapsed=total_elapsed,
    )

    assert len(cases) == EXPECTED_PROFILE_COUNT * EXPECTED_DIALOGUE_COUNT
    assert not profile_errors, f"档案约束提取失败：{profile_errors}"
    assert not dialogue_errors, f"对话约束提取失败：{dialogue_errors}"
    technical_failures = [
        case for case in cases if case.status == "technical_failure"
    ]
    reason_failures = [
        case for case in cases if case.status == "reason_failure"
    ]
    assert not technical_failures, (
        "存在技术失败："
        + json.dumps(
            [asdict(case) for case in technical_failures],
            ensure_ascii=False,
            default=str,
        )
    )
    assert not reason_failures, (
        "存在推荐理由失败："
        + json.dumps(
            [asdict(case) for case in reason_failures],
            ensure_ascii=False,
            default=str,
        )
    )
    assert any(case.status == "recommended" for case in cases), (
        "1000种组合没有一组成功生成推荐理由"
    )
