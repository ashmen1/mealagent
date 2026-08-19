from __future__ import annotations

import copy
import html
import json
import os
import threading
import time
import tomllib
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

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
DRI_PATH = REPO_ROOT / "datas" / "processed" / "Nutrition" / "DRI2023.csv"
REPORT_PATH = (
    REPO_ROOT
    / "docs"
    / "spec_10"
    / "Spec_10_50x20端到端业务报告_统一链路基线.html"
)
CASES_DATA_PATH = (
    REPO_ROOT
    / "tests"
    / ".pytest-tmp"
    / "spec10_50x20_unified_cases.json"
)
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
class ExtractedDialogue:
    """一组真实对话的最终约束及提取度量。"""

    constraints: dict[str, Any]
    llm_calls: int
    attempts: int
    elapsed_seconds: float


@dataclass
class CaseResult:
    """一份档案与一组完整对话通过统一入口后的业务结果。"""

    profile_id: int
    dialogue_id: int
    session_id: int
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
    candidate_attempts: list[dict[str, Any]]
    quality_warnings: list[dict[str, Any]]
    has_explicit_tag_constraints: bool
    elapsed_seconds: float
    detail: str
    generation_result: dict[str, Any] | None


class CountingExtractor:
    """为真实结构化提取器增加线程安全调用计数。"""

    def __init__(self, extractor: Callable[[str], object]) -> None:
        self._extractor = extractor
        self._count = 0
        self._lock = threading.Lock()

    @property
    def count(self) -> int:
        with self._lock:
            return self._count

    def __call__(self, prompt: str) -> object:
        with self._lock:
            self._count += 1
        return self._extractor(prompt)


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


def _load_project_config() -> dict[str, Any]:
    with (REPO_ROOT / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["tool"]["mealagent"]


def _validated_test_database_url(config: dict[str, Any]) -> str:
    test_config = config["test_database"]
    database_url = test_config["url"]
    required_database = test_config["required_database"]
    parsed_url = make_url(database_url)
    if (
        not parsed_url.drivername.startswith("postgresql")
        or parsed_url.database != required_database
    ):
        raise pytest.UsageError(
            f"端到端测试只允许重建隔离测试库{required_database}"
        )
    return database_url


@contextmanager
def _create_test_environment() -> Iterator[SimpleNamespace]:
    """重建隔离测试库，并创建与生产组装方式一致的共享服务。"""

    from backend.application import ConstraintServices
    from backend.infrastructure.database import create_session_factory
    from backend.infrastructure.database.importer import import_basic_data
    from backend.infrastructure.database.models import Base
    from backend.infrastructure.graph import create_neo4j_driver
    from backend.infrastructure.llm import (
        create_langchain_constraint_extractor_from_environment,
    )
    from backend.services import (
        ConstraintConfirmationService,
        ConstraintIntegrationService,
        DialogueConstraintService,
        DishFilteringService,
        MenuPlanningService,
        MenuRecommendationService,
        NutritionService,
        ProfileConstraintService,
        RecommendationReasonService,
    )
    from backend.services.meal_period_resolution import (
        MealPeriodResolutionService,
    )

    config = _load_project_config()
    engine = create_engine(
        _validated_test_database_url(config),
        pool_pre_ping=True,
    )
    graph_config = config["test_neo4j"]
    graph_driver = create_neo4j_driver(
        graph_config["uri"],
        graph_config["user"],
        graph_config["password"],
    )
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
        counting_extractor = CountingExtractor(
            create_langchain_constraint_extractor_from_environment()
        )
        meal_period_service = MealPeriodResolutionService(
            clock=_fixed_clock,
            timezone_name="Asia/Shanghai",
        )
        dialogue_service = DialogueConstraintService(
            session_factory,
            counting_extractor,
            meal_period_service,
        )
        profile_service = ProfileConstraintService(session_factory)
        filtering_service = DishFilteringService(graph_driver)
        confirmation_service = ConstraintConfirmationService(
            dialogue_service,
            meal_period_service,
        )
        integration_service = ConstraintIntegrationService()
        nutrition_service = NutritionService(session_factory)
        planning_service = MenuPlanningService()
        reason_service = RecommendationReasonService()
        recommendation_service = MenuRecommendationService(
            confirmation_service=confirmation_service,
            profile_service=profile_service,
            integration_service=integration_service,
            filtering_service=filtering_service,
            nutrition_service=nutrition_service,
            planning_service=planning_service,
            reason_service=reason_service,
        )
        services = ConstraintServices(
            engine=engine,
            neo4j_driver=graph_driver,
            profile=profile_service,
            dialogue=dialogue_service,
            dish_filtering=filtering_service,
            confirmation=confirmation_service,
            integration=integration_service,
            nutrition=nutrition_service,
            menu_planning=planning_service,
            recommendation_reason=reason_service,
            recommendation=recommendation_service,
        )
        yield SimpleNamespace(
            services=services,
            session_factory=session_factory,
            extractor=counting_extractor,
        )
    finally:
        engine.dispose()
        graph_driver.close()


def _extract_dialogue(
    dialogue: dict[str, Any],
    *,
    profile_id: int,
    services: Any,
    extractor: CountingExtractor,
) -> ExtractedDialogue:
    """所有单轮和多轮均通过同一持久化会话接口提取。"""

    started_at = time.perf_counter()
    initial_calls = extractor.count
    errors: list[str] = []
    for attempt in range(1, 3):
        try:
            session_id = services.dialogue.create_session(profile_id)
            result: dict[str, Any] | None = None
            for message in dialogue["user_messages"]:
                result = services.dialogue.submit_turn(session_id, message)
            assert result is not None
            return ExtractedDialogue(
                constraints=copy.deepcopy(result["merged_constraints"]),
                llm_calls=extractor.count - initial_calls,
                attempts=attempt,
                elapsed_seconds=round(
                    time.perf_counter() - started_at,
                    3,
                ),
            )
        except Exception as exc:
            errors.append(f"第{attempt}次：{type(exc).__name__}：{exc}")
    raise RuntimeError("；".join(errors))


def _seed_generation_sessions(
    session_factory: Callable[[], Session],
    users: list[dict[str, Any]],
    extracted: dict[int, ExtractedDialogue],
) -> dict[tuple[int, int], int]:
    """将已验证的20份结构化状态绑定到50份档案，供统一入口读取。"""

    from backend.infrastructure.database.models import DialogueSession

    session_ids: dict[tuple[int, int], int] = {}
    with session_factory() as session:
        for user in users:
            profile_id = user["id"]
            for dialogue_id, extraction in extracted.items():
                row = DialogueSession(
                    profile_id=profile_id,
                    status="ready_for_planning",
                    merged_constraints=None,
                )
                session.add(row)
                session.flush()
                merged = copy.deepcopy(extraction.constraints)
                merged["dialogue_id"] = row.id
                row.merged_constraints = merged
                session_ids[(profile_id, dialogue_id)] = row.id
        session.commit()
    return session_ids


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
    assert [reason["constraint"] for reason in menu_reasons[:-1]] == (
        expected_health
    )
    nutrition = menu_reasons[-1]
    assert nutrition["reason_type"] == "nutrition_summary"
    assert nutrition["nutrition_score"] == planning_result["nutrition_score"]
    details = nutrition["nutrient_details"]
    assert [detail["nutrient"] for detail in details] == list(NUTRIENT_ORDER)
    assert sum(detail["score"] for detail in details) == (
        planning_result["nutrition_score"]
    )


def _has_explicit_tag_constraints(merged: dict[str, Any]) -> bool:
    if merged["meal_periods"]:
        return True
    return any(
        dish["taste_preferences"]
        or dish["cuisines"]
        or dish["effects"]
        or dish["special_populations"]
        for dish in merged["dishes"]
    )


def _run_case(
    *,
    profile_id: int,
    dialogue_id: int,
    session_id: int,
    profile_constraints: dict[str, Any],
    merged_constraints: dict[str, Any],
    services: Any,
) -> CaseResult:
    """只通过统一推荐入口运行一组组合，并核验终态归因。"""

    started_at = time.perf_counter()
    try:
        generated = services.recommendation.generate(session_id)
        assert generated["session_id"] == session_id
        assert generated["profile_id"] == profile_id
        status = generated["status"]
        confirmation = generated["confirmation_state"]
        planning_context = confirmation.get("planning_context") or {}
        meal_period = planning_context.get("meal_period") or ""
        diner_count = planning_context.get("diner_count")
        filtering = generated["dish_filtering_result"]
        planning = generated["menu_planning_result"]
        reasons = generated["recommendation_reason_result"]

        if status == "empty_candidate":
            assert filtering is not None
            assert generated["empty_dish_indexes"]
            assert all(
                not filtering["dishes"][index]
                for index in generated["empty_dish_indexes"]
            )
        if status == "unmatched_allergen":
            assert generated["unmatched_allergens"]
        if status == "constraint_conflict":
            assert generated["conflicts"]
        if status == "planning_infeasible":
            assert generated["candidate_attempts"]
            assert generated["candidate_attempts"][-1] == {
                **generated["candidate_attempts"][-1],
                "candidate_limit": None,
                "outcome": "infeasible",
                "nutrition_score": None,
            }

        if status == "recommended":
            assert filtering is not None
            assert planning is not None
            assert reasons is not None
            assert generated["candidate_attempts"]
            assert generated["candidate_attempts"][-1]["outcome"] == "accepted"
            score = planning["nutrition_score"]
            if score < 8:
                assert generated["candidate_attempts"][-1][
                    "candidate_limit"
                ] is None
                assert generated["quality_warnings"] == [
                    {
                        "code": "nutrition_score_below_target",
                        "nutrition_score": score,
                        "target_score": 8,
                    }
                ]
            else:
                assert generated["quality_warnings"] == []
            try:
                _assert_recommendation_result(reasons, filtering, planning)
                rebuilt = services.recommendation_reason.build(
                    filtering,
                    planning,
                )
                assert reasons == rebuilt
                assert rebuilt == services.recommendation_reason.build(
                    filtering,
                    planning,
                )
            except Exception as exc:
                return _build_case_result(
                    profile_id=profile_id,
                    dialogue_id=dialogue_id,
                    session_id=session_id,
                    status="reason_failure",
                    profile_constraints=profile_constraints,
                    merged_constraints=merged_constraints,
                    started_at=started_at,
                    detail=f"{type(exc).__name__}：{exc}",
                    generated=generated,
                )

        return _build_case_result(
            profile_id=profile_id,
            dialogue_id=dialogue_id,
            session_id=session_id,
            status=status,
            profile_constraints=profile_constraints,
            merged_constraints=merged_constraints,
            started_at=started_at,
            detail=_case_detail(generated),
            generated=generated,
        )
    except Exception as exc:
        return _build_case_result(
            profile_id=profile_id,
            dialogue_id=dialogue_id,
            session_id=session_id,
            status="technical_failure",
            profile_constraints=profile_constraints,
            merged_constraints=merged_constraints,
            started_at=started_at,
            detail=f"{type(exc).__name__}：{exc}",
            generated=None,
        )


def _case_detail(generated: dict[str, Any]) -> str:
    status = generated["status"]
    if status == "recommended":
        return "统一入口完成菜单规划与推荐理由组装"
    details = {
        "constraint_conflict": generated["conflicts"],
        "unmatched_allergen": generated["unmatched_allergens"],
        "empty_candidate": generated["empty_dish_indexes"],
        "planning_infeasible": generated["candidate_attempts"],
        "needs_confirmation": generated["confirmation_state"].get(
            "confirmation"
        ),
    }
    return json.dumps(details.get(status, status), ensure_ascii=False)


def _build_case_result(
    *,
    profile_id: int,
    dialogue_id: int,
    session_id: int,
    status: str,
    profile_constraints: dict[str, Any],
    merged_constraints: dict[str, Any],
    started_at: float,
    detail: str,
    generated: dict[str, Any] | None,
) -> CaseResult:
    planning = (generated or {}).get("menu_planning_result") or {}
    reasons = (generated or {}).get("recommendation_reason_result") or {}
    dish_recommendations = reasons.get("dish_recommendations", [])
    menu_reasons = reasons.get("menu_reasons", [])
    return CaseResult(
        profile_id=profile_id,
        dialogue_id=dialogue_id,
        session_id=session_id,
        status=status,
        meal_period=(
            ((generated or {}).get("confirmation_state") or {})
            .get("planning_context", {})
            .get("meal_period", "")
        ),
        diner_count=(
            ((generated or {}).get("confirmation_state") or {})
            .get("planning_context", {})
            .get("diner_count")
        ),
        special_populations=list(profile_constraints["special_populations"]),
        allergens=list(profile_constraints["allergens"]),
        selected_recipes=[
            item["recipe_name"] for item in planning.get("selected_dishes", [])
        ],
        dish_reason_counts=[
            len(item["reasons"]) for item in dish_recommendations
        ],
        tag_groups=[
            reason["matched_group"]
            for dish in dish_recommendations
            for reason in dish["reasons"]
        ],
        health_constraints=[
            reason["constraint"]
            for reason in menu_reasons
            if reason["reason_type"] == "health_constraint"
        ],
        nutrition_score=planning.get("nutrition_score"),
        total_reason_count=sum(
            len(item["reasons"]) for item in dish_recommendations
        )
        + len(menu_reasons),
        candidate_attempts=copy.deepcopy(
            (generated or {}).get("candidate_attempts", [])
        ),
        quality_warnings=copy.deepcopy(
            (generated or {}).get("quality_warnings", [])
        ),
        has_explicit_tag_constraints=_has_explicit_tag_constraints(
            merged_constraints
        ),
        elapsed_seconds=round(time.perf_counter() - started_at, 4),
        detail=detail,
        generation_result=_compact_generation_result(generated),
    )


def _compact_generation_result(
    generated: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """报告仅保留候选计数与入选证据，避免重复嵌入全部未入选菜谱。"""

    if generated is None:
        return None
    compact = copy.deepcopy(generated)
    filtering = compact.pop("dish_filtering_result", None)
    planning = compact.get("menu_planning_result") or {}
    selected = planning.get("selected_dishes", [])
    if filtering is None:
        compact["dish_filtering_audit"] = None
        return compact

    selected_candidates = []
    for selected_dish in selected:
        dish_index = selected_dish["dish_constraint_index"]
        recipe_name = selected_dish["recipe_name"]
        candidates = filtering["dishes"][dish_index]
        candidate_index, candidate = next(
            (index, item)
            for index, item in enumerate(candidates)
            if item["recipe_name"] == recipe_name
        )
        selected_candidates.append(
            {
                "dish_constraint_index": dish_index,
                "candidate_index": candidate_index,
                "candidate": candidate,
            }
        )
    compact["dish_filtering_audit"] = {
        "profile_id": generated["profile_id"],
        "dialogue_id": generated["dialogue_id"],
        "candidate_counts": [
            len(candidates) for candidates in filtering["dishes"]
        ],
        "unmatched_allergens": filtering["unmatched_allergens"],
        "selected_candidates": selected_candidates,
    }
    return compact


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _status_label(status: str) -> str:
    return {
        "recommended": "推荐成功",
        "constraint_conflict": "约束冲突",
        "needs_confirmation": "餐次待确认",
        "unmatched_allergen": "过敏原未匹配",
        "empty_candidate": "全量候选为空",
        "planning_infeasible": "全量规划无解",
        "reason_failure": "推荐理由失败",
        "technical_failure": "技术失败",
    }.get(status, status)


def _status_class(status: str) -> str:
    if status == "recommended":
        return "ok"
    if status in {
        "constraint_conflict",
        "needs_confirmation",
        "unmatched_allergen",
        "empty_candidate",
        "planning_infeasible",
    }:
        return "blocked"
    return "fail"


def _generate_report(
    *,
    users: list[dict[str, Any]],
    dialogues: list[dict[str, Any]],
    cases: list[CaseResult],
    extracted: dict[int, ExtractedDialogue],
    dialogue_errors: dict[int, str],
    environment: dict[str, Any],
    total_elapsed: float,
) -> None:
    """生成与历史报告同一视觉口径的统一链路基线报告。"""

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    counts = Counter(case.status for case in cases)
    by_dialogue: dict[int, list[CaseResult]] = defaultdict(list)
    by_profile: dict[int, list[CaseResult]] = defaultdict(list)
    for case in cases:
        by_dialogue[case.dialogue_id].append(case)
        by_profile[case.profile_id].append(case)

    recommended = [case for case in cases if case.status == "recommended"]
    tag_counts = Counter(group for case in recommended for group in case.tag_groups)
    health_counts = Counter(
        item for case in recommended for item in case.health_constraints
    )
    score_counts = Counter(
        case.nutrition_score
        for case in recommended
        if case.nutrition_score is not None
    )
    zero_reason_dishes = sum(
        count == 0 for case in recommended for count in case.dish_reason_counts
    )
    zero_reason_alerts = sum(
        count == 0
        for case in recommended
        if case.has_explicit_tag_constraints
        for count in case.dish_reason_counts
    )
    expanded_cases = sum(len(case.candidate_attempts) > 1 for case in cases)
    full_attempt_cases = sum(
        any(attempt["candidate_limit"] is None for attempt in case.candidate_attempts)
        for case in cases
    )
    low_score_cases = sum(bool(case.quality_warnings) for case in recommended)

    dialogue_by_id = {dialogue["id"]: dialogue for dialogue in dialogues}
    dialogue_rows = []
    for dialogue_id in sorted(dialogue_by_id):
        rows = by_dialogue.get(dialogue_id, [])
        status_counts = Counter(row.status for row in rows)
        extraction = extracted.get(dialogue_id)
        constraints = extraction.constraints if extraction else {}
        dialogue_rows.append(
            "<tr>"
            f"<td>{dialogue_id}</td>"
            f"<td>{_escape(' / '.join(dialogue_by_id[dialogue_id]['user_messages']))}</td>"
            f"<td>{dialogue_by_id[dialogue_id]['turn_count']}</td>"
            f"<td>{_escape(constraints.get('meal_periods', '-'))}</td>"
            f"<td>{_escape(constraints.get('diner_count', '-'))}</td>"
            f"<td>{extraction.llm_calls if extraction else 0}</td>"
            f"<td>{extraction.attempts if extraction else 0}</td>"
            f"<td>{extraction.elapsed_seconds if extraction else 0:.3f}s</td>"
            f"<td>{status_counts.get('recommended', 0)}</td>"
            f"<td>{_escape(', '.join(f'{_status_label(key)}={value}' for key, value in status_counts.items() if key != 'recommended') or dialogue_errors.get(dialogue_id, '-'))}</td>"
            "</tr>"
        )

    user_by_id = {user["id"]: user for user in users}
    profile_rows = []
    for profile_id in sorted(by_profile):
        rows = by_profile[profile_id]
        status_counts = Counter(row.status for row in rows)
        user = user_by_id[profile_id]
        profile_rows.append(
            "<tr>"
            f"<td>{profile_id}</td>"
            f"<td>{_escape(user.get('性别', '-'))} / {_escape(user.get('年龄', '-'))}</td>"
            f"<td>{_escape(rows[0].special_populations)}</td>"
            f"<td>{status_counts.get('recommended', 0)}</td>"
            f"<td>{_escape(', '.join(f'{_status_label(key)}={value}' for key, value in status_counts.items() if key != 'recommended') or '-')}</td>"
            f"<td>{sum(row.elapsed_seconds for row in rows) / len(rows):.3f}s</td>"
            "</tr>"
        )

    case_rows = []
    detail_blocks = []
    for case in cases:
        attempt_text = " → ".join(
            f"{attempt['candidate_limit'] if attempt['candidate_limit'] is not None else '全量'}:{attempt['candidate_counts']}:{attempt['outcome']}:{attempt['nutrition_score']}"
            for attempt in case.candidate_attempts
        ) or "-"
        case_rows.append(
            "<tr>"
            f"<td>{case.profile_id}</td><td>{case.dialogue_id}</td>"
            f"<td>{case.session_id}</td>"
            f'<td><span class="status {_status_class(case.status)}">{_escape(_status_label(case.status))}</span></td>'
            f"<td>{_escape(case.meal_period or '-')}</td>"
            f"<td>{_escape(case.selected_recipes or '-')}</td>"
            f"<td>{_escape(case.dish_reason_counts or '-')}</td>"
            f"<td>{_escape(case.nutrition_score if case.nutrition_score is not None else '-')}</td>"
            f"<td>{_escape(attempt_text)}</td>"
            f"<td>{_escape(case.quality_warnings or '-')}</td>"
            f"<td>{case.elapsed_seconds:.4f}s</td>"
            f"<td>{_escape(case.detail)}</td></tr>"
        )
        if case.generation_result is not None:
            detail_blocks.append(
                "<details>"
                f"<summary>档案{case.profile_id} × 对话{case.dialogue_id}：{_escape(_status_label(case.status))}</summary>"
                f"<pre>{_escape(json.dumps(case.generation_result, ensure_ascii=False, indent=2, default=str))}</pre>"
                "</details>"
            )

    score_rows = "".join(
        f"<tr><td>{score}</td><td>{score_counts[score]}</td></tr>"
        for score in sorted(score_counts)
    )
    pass_status = (
        "通过"
        if not counts.get("technical_failure")
        and not counts.get("reason_failure")
        and not dialogue_errors
        and recommended
        else "未通过"
    )
    pass_class = "ok" if pass_status == "通过" else "fail"
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")

    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Spec_10 50×20 端到端业务报告：统一链路基线</title>
<style>
:root {{ color-scheme:light; --ink:#17202a; --muted:#65717e; --line:#dce3ea; --brand:#155eef; --ok:#087a55; --warn:#9a6700; --fail:#c62828; }}
* {{ box-sizing:border-box; }} body {{ margin:0;font:14px/1.55 "Segoe UI","Microsoft YaHei",sans-serif;color:var(--ink);background:#f5f7fa; }}
header {{ padding:36px max(24px,5vw);color:white;background:linear-gradient(120deg,#0c3175,#155eef); }} header h1 {{ margin:0 0 8px;font-size:30px; }} header p {{ margin:4px 0;opacity:.9; }}
main {{ max-width:1680px;margin:auto;padding:24px; }} section {{ margin:0 0 24px;padding:22px;background:white;border:1px solid var(--line);border-radius:12px;box-shadow:0 4px 16px #23395d0c; }} h2 {{ margin-top:0;font-size:21px; }}
.cards {{ display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px; }} .card {{ padding:14px;border:1px solid var(--line);border-radius:10px;background:#fbfcfe; }} .card span {{ display:block;color:var(--muted); }} .card strong {{ display:block;margin-top:4px;font-size:25px; }}
.note {{ padding:12px 14px;border-left:4px solid var(--brand);background:#eef4ff; }} .warn-note {{ border-left-color:var(--warn);background:#fff8e6; }} .table-wrap {{ overflow:auto;max-height:720px;border:1px solid var(--line);border-radius:8px; }}
table {{ width:100%;border-collapse:collapse;white-space:nowrap; }} th,td {{ padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top; }} th {{ position:sticky;top:0;z-index:1;background:#edf2f8; }} tr:hover td {{ background:#f8fbff; }}
.status {{ display:inline-block;padding:2px 8px;border-radius:99px;font-weight:600; }} .status.ok {{ color:var(--ok);background:#e8f7f1; }} .status.blocked {{ color:var(--warn);background:#fff4d6; }} .status.fail {{ color:var(--fail);background:#ffebee; }} pre {{ overflow:auto;padding:12px;border:1px solid var(--line);border-radius:8px;background:#f7f9fb;white-space:pre-wrap; }} details {{ margin:8px 0; }} summary {{ cursor:pointer;font-weight:600; }}
</style></head><body>
<header><h1>Spec_10 推荐链路：50份档案 × 20组完整对话</h1><p>统一持久化对话 + 统一推荐入口 + 全量候选扩展 + 固定模板推荐理由</p><p>生成时间：{generated_at}；总耗时：{total_elapsed:.3f}秒</p></header>
<main>
<section><h2>执行口径</h2><p class="note">20组对话各自通过统一持久化接口完整提取一次，再把已验证结构化状态分别绑定到50份档案；1000组下游生成全部只调用统一入口。未明确餐次固定使用上海时间12:00解析。规划候选按100、300、全量确定性扩展，不再随机抽样。</p><ul>
<li>组合：{len(users)}份档案 × {len(dialogues)}组对话 = {len(cases)}组；14组单轮与6组多轮共{sum(item['turn_count'] for item in dialogues)}轮。</li>
<li>基础设施：PostgreSQL测试库{environment['database']}；{environment['profiles']}份档案、{environment['postgres_recipes']}道菜谱、{environment['recipe_nutrition']}份营养数据；Neo4j菜谱节点{environment['neo4j_recipes']}。</li>
<li>LLM：{_escape(environment['llm_provider'])} / {_escape(environment['llm_model'])}；实际调用{sum(item.llm_calls for item in extracted.values())}次；每组外部失败时最多从新会话重试一次。</li>
<li>归因门禁：空候选必须来自完整筛选结果；规划无解必须经过全量候选；低于8分必须经过全量候选并返回结构化警告。</li>
</ul></section>
<section><h2>结果总览</h2><div class="cards">
<div class="card"><span>验收结论</span><strong><span class="status {pass_class}">{pass_status}</span></strong></div><div class="card"><span>总组合</span><strong>{len(cases)}</strong></div>
<div class="card"><span>推荐成功</span><strong>{counts.get('recommended',0)}</strong></div><div class="card"><span>技术失败</span><strong>{counts.get('technical_failure',0)}</strong></div><div class="card"><span>理由失败</span><strong>{counts.get('reason_failure',0)}</strong></div>
<div class="card"><span>发生候选扩展</span><strong>{expanded_cases}</strong></div><div class="card"><span>尝试全量候选</span><strong>{full_attempt_cases}</strong></div><div class="card"><span>低于8分警告</span><strong>{low_score_cases}</strong></div>
<div class="card"><span>入选菜品</span><strong>{sum(len(case.selected_recipes) for case in recommended)}</strong></div><div class="card"><span>无标签理由菜品</span><strong>{zero_reason_dishes}</strong></div><div class="card"><span>显式标签下仍无理由</span><strong>{zero_reason_alerts}</strong></div>
</div><p class="note warn-note">“显式标签下仍无理由”是业务质量提醒，不伪造理由；需要结合菜谱标签质量继续治理。</p></section>
<section><h2>终态分布</h2><div class="cards">{''.join(f'<div class="card"><span>{_status_label(key)}</span><strong>{value}</strong></div>' for key,value in counts.items())}</div></section>
<section><h2>理由与营养覆盖</h2><div class="cards">{''.join(f'<div class="card"><span>{group}标签理由</span><strong>{tag_counts.get(group,0)}</strong></div>' for group in TAG_GROUP_ORDER)}<div class="card"><span>健康约束理由</span><strong>{sum(health_counts.values())}</strong></div></div><h3>营养得分分布</h3><div class="table-wrap"><table><thead><tr><th>得分（满分16）</th><th>菜单数</th></tr></thead><tbody>{score_rows}</tbody></table></div></section>
<section><h2>按对话汇总</h2><div class="table-wrap"><table><thead><tr><th>ID</th><th>完整原文</th><th>轮数</th><th>餐次</th><th>人数</th><th>LLM调用</th><th>尝试</th><th>耗时</th><th>推荐成功</th><th>其他终态</th></tr></thead><tbody>{''.join(dialogue_rows)}</tbody></table></div></section>
<section><h2>按用户档案汇总</h2><div class="table-wrap"><table><thead><tr><th>档案ID</th><th>性别 / 年龄</th><th>特殊人群</th><th>推荐成功</th><th>其他终态</th><th>平均耗时</th></tr></thead><tbody>{''.join(profile_rows)}</tbody></table></div></section>
<section><h2>{len(cases)}组端到端明细</h2><div class="table-wrap"><table><thead><tr><th>档案</th><th>对话</th><th>会话</th><th>状态</th><th>餐次</th><th>入选菜</th><th>理由数</th><th>营养分</th><th>候选尝试：上限/数量/结果/得分</th><th>警告</th><th>耗时</th><th>详情</th></tr></thead><tbody>{''.join(case_rows)}</tbody></table></div></section>
<section><h2>结构化审计输出</h2><p>展开后可审计确认状态、各组候选总数、入选候选原始索引与标签证据、候选扩展、最终菜单、来源路径和营养明细；未重复嵌入未入选菜谱明细。</p>{''.join(detail_blocks)}</section>
<section><h2>结论</h2><ul><li>技术失败：{counts.get('technical_failure',0)}；推荐理由失败：{counts.get('reason_failure',0)}；对话提取失败：{len(dialogue_errors)}。</li><li>全量候选为空与全量规划无解均已按真实终态单独归因。</li><li>所有成功菜单的理由均重新核对最终选择、筛选标签、健康约束和8项营养明细，并验证固定模板组装的确定性。</li></ul></section>
</main></body></html>"""
    REPORT_PATH.write_text(document, encoding="utf-8")


def test_报告压缩从统一结果读取标识而不扩充筛选契约() -> None:
    generated = {
        "profile_id": 7,
        "dialogue_id": 11,
        "dish_filtering_result": {
            "dishes": [
                [
                    {
                        "recipe_name": "番茄炒蛋",
                        "matched_tags": ["午餐"],
                        "matched_groups": ["餐次"],
                    }
                ]
            ],
            "unmatched_allergens": [],
        },
        "menu_planning_result": {
            "selected_dishes": [
                {
                    "dish_constraint_index": 0,
                    "recipe_name": "番茄炒蛋",
                }
            ]
        },
    }

    compact = _compact_generation_result(generated)

    assert compact is not None
    assert "dish_filtering_result" not in compact
    assert compact["dish_filtering_audit"] == {
        "profile_id": 7,
        "dialogue_id": 11,
        "candidate_counts": [1],
        "unmatched_allergens": [],
        "selected_candidates": [
            {
                "dish_constraint_index": 0,
                "candidate_index": 0,
                "candidate": {
                    "recipe_name": "番茄炒蛋",
                    "matched_tags": ["午餐"],
                    "matched_groups": ["餐次"],
                },
            }
        ],
    }
    assert "profile_id" not in generated["dish_filtering_result"]


@pytest.mark.integration
def test_50份真实档案与20组完整对话贯通统一推荐入口() -> None:
    """运行1000种组合，验证单轮和多轮均贯通统一推荐链路。"""

    _load_dotenv()
    ensure_graph_data()
    users = _load_json_array(USERS_PATH)
    dialogues = _load_json_array(DIALOGUES_PATH)
    assert len(users) == EXPECTED_PROFILE_COUNT
    assert len(dialogues) == EXPECTED_DIALOGUE_COUNT

    from backend.infrastructure.database.models import (
        Recipe,
        RecipeNutrition,
        UserProfile,
    )

    started_at = time.perf_counter()
    extracted: dict[int, ExtractedDialogue] = {}
    dialogue_errors: dict[int, str] = {}
    cases: list[CaseResult] = []

    with _create_test_environment() as environment:
        services = environment.services
        session_factory = environment.session_factory
        with session_factory() as session:
            postgres_counts = {
                "profiles": session.scalar(select(func.count(UserProfile.id))),
                "postgres_recipes": session.scalar(select(func.count(Recipe.id))),
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

        profile_constraints = {
            user["id"]: services.profile.extract(user["id"])
            for user in users
        }
        for dialogue in dialogues:
            try:
                extracted[dialogue["id"]] = _extract_dialogue(
                    dialogue,
                    profile_id=users[0]["id"],
                    services=services,
                    extractor=environment.extractor,
                )
            except Exception as exc:
                dialogue_errors[dialogue["id"]] = (
                    f"{type(exc).__name__}：{exc}"
                )

        session_ids = _seed_generation_sessions(
            session_factory,
            users,
            extracted,
        )
        tasks = [
            {
                "profile_id": user["id"],
                "dialogue_id": dialogue["id"],
                "session_id": session_ids[(user["id"], dialogue["id"])],
                "profile_constraints": profile_constraints[user["id"]],
                "merged_constraints": extracted[dialogue["id"]].constraints,
                "services": services,
            }
            for user in users
            for dialogue in dialogues
            if dialogue["id"] in extracted
        ]
        with ThreadPoolExecutor(max_workers=8) as pool:
            cases = list(pool.map(lambda task: _run_case(**task), tasks))

        project_config = _load_project_config()
        report_environment = {
            **postgres_counts,
            "database": make_url(
                project_config["test_database"]["url"]
            ).database,
            "neo4j_recipes": neo4j_recipes,
            "llm_provider": os.environ.get("LLM_PROVIDER", "未配置"),
            "llm_model": os.environ.get("LLM_MODEL", "未配置"),
        }

    total_elapsed = time.perf_counter() - started_at
    report_data = {
        "users": users,
        "dialogues": dialogues,
        "extracted": {
            key: asdict(value) for key, value in extracted.items()
        },
        "dialogue_errors": dialogue_errors,
        "cases": [asdict(case) for case in cases],
        "environment": report_environment,
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
        extracted=extracted,
        dialogue_errors=dialogue_errors,
        environment=report_environment,
        total_elapsed=total_elapsed,
    )

    assert len(extracted) == EXPECTED_DIALOGUE_COUNT, (
        "完整对话提取失败："
        + json.dumps(dialogue_errors, ensure_ascii=False)
    )
    assert len(cases) == EXPECTED_PROFILE_COUNT * EXPECTED_DIALOGUE_COUNT
    technical_failures = [
        asdict(case) for case in cases if case.status == "technical_failure"
    ]
    reason_failures = [
        asdict(case) for case in cases if case.status == "reason_failure"
    ]
    assert not technical_failures, (
        "存在技术失败："
        + json.dumps(technical_failures, ensure_ascii=False, default=str)
    )
    assert not reason_failures, (
        "存在推荐理由失败："
        + json.dumps(reason_failures, ensure_ascii=False, default=str)
    )
    assert any(case.status == "recommended" for case in cases), (
        "1000种组合没有一组成功生成菜单"
    )
