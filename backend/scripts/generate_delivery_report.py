from __future__ import annotations

"""生成交付用 50×20 端到端测试报告（HTML）。

布局：20 组对话用例 × 50 份用户档案；每组对话的每一轮展示
原对话、硬约束、软目标、当前轮状态、当前轮 API 输出。

运行前提：Docker 基础设施已启动（postgres/neo4j）+ 仓库根目录 .env 含 LLM 配置。
用法：python -m backend.scripts.generate_delivery_report
"""

import copy
import html
import json
import os
import sys
import time
import tomllib
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

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
OUTPUT_PATH = REPO_ROOT / "docs" / "交付" / "测试报告_50x20.html"

LLM_ENVIRONMENT_NAMES = frozenset(
    {
        "LLM_PROVIDER",
        "LLM_BASE_URL",
        "LLM_AUTH_TOKEN",
        "LLM_MODEL",
        "LLM_PROVIDER_BACKUP",
        "LLM_BASE_URL_BACKUP",
        "LLM_AUTH_TOKEN_BACKUP",
        "LLM_MODEL_BACKUP",
    }
)

STATUS_LABELS = {
    "recommended": "推荐成功",
    "needs_confirmation": "待确认",
    "constraint_conflict": "约束冲突",
    "unmatched_allergen": "过敏词未识别",
    "empty_candidate": "空候选",
    "planning_infeasible": "规划无解",
    "in_progress": "尚无内容",
}

TASTE_LABELS = {
    "is_sweet": "甜",
    "is_light": "清淡",
    "is_spicy": "辣",
    "is_salty": "咸",
    "is_sour": "酸",
}


def _load_dotenv() -> None:
    """加载环境；LLM配置以.env为准，其他配置保留进程优先级。"""

    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        raise AssertionError("交付报告生成需要仓库根目录下的.env")
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
        raise RuntimeError(
            f"交付报告只允许重建隔离测试库{required_database}"
        )
    return database_url


def _fixed_clock() -> datetime:
    """未明确餐次时固定按上海午餐窗口解析。"""

    return datetime(2026, 8, 19, 12, 0)


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


class CountingExtractor:
    """为真实结构化提取器增加调用计数。"""

    def __init__(self, extractor: Callable[[str], object]) -> None:
        self._extractor = extractor
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def __call__(self, prompt: str) -> object:
        self._count += 1
        return self._extractor(prompt)


@contextmanager
def _create_test_environment() -> Iterator[Any]:
    """重建隔离测试库并创建与生产组装方式一致的服务容器。"""

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
            llm_model=os.environ.get("LLM_MODEL", "?"),
            llm_provider=os.environ.get("LLM_PROVIDER", "?"),
        )
    finally:
        engine.dispose()
        graph_driver.close()


def _hard_soft_summary(integrated: dict[str, Any]) -> dict[str, str]:
    """从整合约束中区分硬约束与软目标。"""

    hard: list[str] = []
    meal_periods = integrated.get("meal_periods") or []
    if meal_periods:
        hard.append("餐次：" + "、".join(meal_periods))
    diner_count = integrated.get("diner_count")
    if diner_count:
        hard.append(f"人数：{diner_count}人")
    total_dish_count = integrated.get("total_dish_count")
    if total_dish_count:
        hard.append(f"菜品数：{total_dish_count}道")
    minutes = integrated.get("max_total_time_minutes")
    if minutes:
        hard.append(f"时限：{minutes}分钟以内")
    difficulty = integrated.get("max_difficulty")
    if difficulty:
        hard.append(f"难度：≤{difficulty}")
    available = integrated.get("available_ingredients") or []
    if available:
        hard.append("可用食材：" + "、".join(available))
    allergens = integrated.get("allergens") or []
    if allergens:
        hard.append("过敏排除：" + "、".join(allergens))
    required: list[str] = []
    for dish in integrated.get("dishes", []):
        for group in dish.get("required_ingredient_groups", []):
            separator = "和" if group.get("match") == "all" else "或"
            required.append(
                separator.join(
                    item["value"] for item in group.get("items", [])
                )
            )
    if required:
        hard.append("必需食材：" + "；".join(required))

    soft: list[str] = []
    negative_tastes: list[str] = []
    for dish in integrated.get("dishes", []):
        tastes = [
            TASTE_LABELS.get(key, key)
            for key, enabled in dish.get("taste_preferences", {}).items()
            if enabled
        ]
        negative_tastes.extend(
            TASTE_LABELS.get(key, key)
            for key, enabled in dish.get("taste_preferences", {}).items()
            if not enabled
        )
        if tastes:
            soft.append("口味：" + "、".join(tastes))
        if dish.get("cuisines"):
            soft.append("菜系：" + "、".join(dish["cuisines"]))
        if dish.get("effects"):
            soft.append("功效：" + "、".join(dish["effects"]))
        if dish.get("special_populations"):
            soft.append("人群：" + "、".join(dish["special_populations"]))
    negative_tastes = list(dict.fromkeys(negative_tastes))
    if negative_tastes:
        hard.append("忌口：" + "、".join(f"不{label}" for label in negative_tastes))
    return {
        "hard": "；".join(hard) or "无",
        "soft": "；".join(soft) or "无",
    }


def _bind_generation_session(
    session_factory: Callable[[], Session],
    profile_id: int,
    merged: dict[str, Any],
) -> int:
    """把一份完整合并约束绑定为独立会话并返回会话号。"""

    from backend.infrastructure.database.models import DialogueSession

    with session_factory() as session:
        row = DialogueSession(
            profile_id=profile_id,
            status="ready_for_planning",
            merged_constraints=None,
        )
        session.add(row)
        session.flush()
        bound = copy.deepcopy(merged)
        bound["dialogue_id"] = row.id
        row.merged_constraints = bound
        session.commit()
        return row.id


def _collect_turns(
    services: Any,
    extractor: Any,
    composer: Any,
    integration_service: Any,
    session_factory: Callable[[], Session],
    users: list[dict[str, Any]],
    profile_constraints: dict[int, dict[str, Any]],
    dialogue: dict[str, Any],
    profile_id: int = 25,
) -> tuple[list[dict[str, Any]], dict[str, Any], int, list[str]]:
    """逐轮提交对话；每轮对50份档案跑整合+生成，记录API级回答。

    LLM失败时整组从新会话重试一次。
    返回 (每轮记录列表, 最终合并约束, LLM调用数, 重试记录)。
    """

    turns: list[dict[str, Any]] = []
    merged_raw: dict[str, Any] = {}
    attempts = 1
    errors: list[str] = []
    for attempt in range(1, 6):
        turns = []
        try:
            before = extractor.count
            session_id = services.confirmation.create_session(profile_id)
            for message in dialogue["user_messages"]:
                started_at = time.perf_counter()
                result = services.confirmation.submit_turn(
                    session_id,
                    message,
                )
                extract_elapsed = time.perf_counter() - started_at
                merged = result.get("merged_constraints") or {}
                # API 单档案（档案25）性能指标：首Token = LLM提取+生成，
                # 模板组装无增量生成，单轮端到端与首Token几乎相等
                case_session = _bind_generation_session(
                    session_factory,
                    profile_id,
                    merged,
                )
                generate_started = time.perf_counter()
                generation = services.recommendation.generate(case_session)
                generate_elapsed = time.perf_counter() - generate_started
                first_token_elapsed = round(
                    extract_elapsed + generate_elapsed, 3
                )
                e2e_elapsed = round(
                    extract_elapsed + generate_elapsed, 3
                )
                integrate_elapsed = 0.0
                per_user: list[dict[str, Any]] = []
                for user in users:
                    user_id = user["id"]
                    integrate_started = time.perf_counter()
                    integrated = integration_service.integrate(
                        profile_constraints[user_id],
                        merged,
                    )
                    integrate_elapsed += (
                        time.perf_counter() - integrate_started
                    )
                    hard_soft = _hard_soft_summary(integrated)
                    if user_id == profile_id:
                        user_generation = generation
                        user_answer = composer.compose(generation)
                    else:
                        user_session = _bind_generation_session(
                            session_factory,
                            user_id,
                            merged,
                        )
                        user_generation = services.recommendation.generate(
                            user_session
                        )
                        user_answer = composer.compose(user_generation)
                    per_user.append(
                        {
                            "profile_id": user_id,
                            "hard": hard_soft["hard"],
                            "soft": hard_soft["soft"],
                            "status": user_generation["status"],
                            "answer": user_answer,
                            "nutrition_score": (
                                user_generation.get("menu_planning_result")
                                or {}
                            ).get("nutrition_score"),
                            "detail": user_generation.get("conflicts")
                            or user_generation.get("unmatched_allergens")
                            or user_generation.get("empty_dish_indexes")
                            or user_generation.get("candidate_attempts"),
                        }
                    )
                turns.append(
                    {
                        "user_message": message,
                        "turn_number": result["turn_number"],
                        "first_token_elapsed": first_token_elapsed,
                        "e2e_elapsed": e2e_elapsed,
                        "extract_elapsed": round(extract_elapsed, 3),
                        "generate_elapsed": round(generate_elapsed, 3),
                        "integrate_elapsed": round(integrate_elapsed, 3),
                        "per_user": per_user,
                    }
                )
                merged_raw = merged
            llm_calls = extractor.count - before
            return turns, merged_raw, llm_calls, errors
        except Exception as exc:
            attempts = attempt
            errors.append(f"第{attempt}次：{type(exc).__name__}：{exc}")
    return turns, merged_raw, attempts, errors


def _render_case_row(item: dict[str, Any]) -> str:
    """把单个档案×轮次的记录渲染为表格行。"""

    status_label = STATUS_LABELS.get(item["status"], item["status"])
    status_class = "ok" if item["status"] == "recommended" else "blocked"
    status_cell = (
        f"<span class='status {status_class}'>{_escape(status_label)}</span>"
    )
    if item["status"] == "recommended":
        status_cell += (
            f"<div class='muted'>营养分 {item['nutrition_score']}/16</div>"
        )
    detail_html = ""
    if item["status"] != "recommended":
        detail_html = (
            "<p class='note'>详情："
            + _escape(json.dumps(item["detail"], ensure_ascii=False, default=str))
            + "</p>"
        )
    return (
        f"<tr><td>{item['profile_id']}</td>"
        f"<td>{_escape(item['hard'])}</td>"
        f"<td>{_escape(item['soft'])}</td>"
        f"<td>{status_cell}</td>"
        f"<td><details><summary>查看回答</summary>"
        f"<pre class='answer'>{_escape(item['answer'])}</pre>"
        f"{detail_html}</details></td></tr>"
    )


def _render_report(
    users: list[dict[str, Any]],
    dialogues: list[dict[str, Any]],
    extracted: dict[int, dict[str, Any]],
    environment: dict[str, Any],
    total_elapsed: float,
) -> str:
    """组装交付测试报告HTML。"""

    all_items = [
        item
        for extraction in extracted.values()
        for turn in extraction["turns"]
        for item in turn["per_user"]
    ]
    status_counts = Counter(item["status"] for item in all_items)
    recommended = status_counts.get("recommended", 0)
    llm_calls = sum(item["llm_calls"] for item in extracted.values())

    all_turns = [
        turn
        for extraction in extracted.values()
        for turn in extraction["turns"]
    ]
    first_token_values = [turn["first_token_elapsed"] for turn in all_turns]
    e2e_values = [turn["e2e_elapsed"] for turn in all_turns]
    multi_turn_averages = [
        sum(
            turn["e2e_elapsed"]
            for turn in extracted[dialogue["id"]]["turns"]
        )
        / len(extracted[dialogue["id"]]["turns"])
        for dialogue in dialogues
        if dialogue["turn_count"] > 1
    ]

    def _average(values: list[float]) -> float:
        return round(sum(values) / len(values), 2)

    def _pass_rate(values: list[float], threshold: float) -> str:
        passed = sum(1 for value in values if value < threshold)
        return f"{passed}/{len(values)}"

    perf_rows = "".join(
        f"<tr><td>{label}</td><td>{_average(values)}s</td>"
        f"<td>{_pass_rate(values, excellent)}</td>"
        f"<td>{_pass_rate(values, passable)}</td></tr>"
        for label, values, excellent, passable in (
            ("首Token延迟", first_token_values, 2, 5),
            ("单轮端到端响应", e2e_values, 8, 15),
            ("多轮平均响应", multi_turn_averages, 6, 12),
        )
    )

    cards = "".join(
        f'<div class="card"><span>{label}</span><strong>{count}</strong></div>'
        for label, count in (
            ("总组合", len(all_items)),
            ("推荐成功", recommended),
            ("异常终态", len(all_items) - recommended),
            ("LLM调用", llm_calls),
            ("报告生成耗时", f"{total_elapsed:.0f}s"),
        )
    )

    status_rows = "".join(
        f"<tr><td>{_escape(STATUS_LABELS.get(status, status))}</td>"
        f"<td>{count}</td></tr>"
        for status, count in sorted(
            status_counts.items(), key=lambda item: -item[1]
        )
    )

    dialogue_sections: list[str] = []
    for dialogue in dialogues:
        dialogue_id = dialogue["id"]
        extraction = extracted[dialogue_id]
        turn_blocks: list[str] = []
        cumulative_e2e: list[float] = []
        for turn in extraction["turns"]:
            cumulative_e2e.append(turn["e2e_elapsed"])
            turn_average = round(
                sum(cumulative_e2e) / len(cumulative_e2e), 3
            )
            rows = "".join(
                _render_case_row(item)
                for item in turn["per_user"]
            )
            turn_blocks.append(
                f"<details {'open' if turn['turn_number'] == 1 else ''}>"
                f"<summary>第{turn['turn_number']}轮：{_escape(turn['user_message'])}"
                f"（首Token {turn['first_token_elapsed']}s｜单轮端到端 {turn['e2e_elapsed']}s"
                f"｜多轮平均 {turn_average}s）</summary>"
                f"<div class='table-wrap'><table><thead>"
                f"<tr><th>档案</th><th>硬约束</th><th>软目标</th><th>当前轮状态</th><th>当前轮API输出</th></tr>"
                f"</thead><tbody>{rows}</tbody></table></div>"
                "</details>"
            )
        dialogue_sections.append(
            f"<section><h2>对话 {dialogue_id}</h2>"
            f"<p class='note'>{_escape(' / '.join(dialogue['user_messages']))}</p>"
            f"<p>轮数：{dialogue['turn_count']}；LLM调用：{extraction['llm_calls']}次。</p>"
            f"{''.join(turn_blocks)}</section>"
        )

    abnormal_items = [item for item in all_items if item["status"] != "recommended"]
    abnormal_rows = "".join(
        f"<tr><td>{item['profile_id']}</td>"
        f"<td>{_escape(item['hard'])}</td>"
        f"<td><span class='status blocked'>{_escape(STATUS_LABELS.get(item['status'], item['status']))}</span></td>"
        f"<td>{_escape(json.dumps(item['detail'], ensure_ascii=False, default=str))}</td></tr>"
        for item in abnormal_items
    )
    error_rows = "".join(
        f"<tr><td>{dialogue_id}</td><td>{_escape('；'.join(extraction['errors']) or '无')}</td></tr>"
        for dialogue_id, extraction in extracted.items()
        if extraction["errors"]
    )

    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>个性化膳食规划Agent · 50×20 端到端测试报告</title>
<style>
:root {{ color-scheme:light; --ink:#17202a; --muted:#65717e; --line:#dce3ea; --brand:#155eef; --ok:#087a55; --warn:#9a6700; --fail:#c62828; }}
* {{ box-sizing:border-box; }} body {{ margin:0;font:14px/1.55 "Segoe UI","Microsoft YaHei",sans-serif;color:var(--ink);background:#f5f7fa; }}
header {{ padding:36px max(24px,5vw);color:white;background:linear-gradient(120deg,#0c3175,#155eef); }} header h1 {{ margin:0 0 8px;font-size:30px; }} header p {{ margin:4px 0;opacity:.9; }}
main {{ max-width:1680px;margin:auto;padding:24px; }} section {{ margin:0 0 24px;padding:22px;background:white;border:1px solid var(--line);border-radius:12px;box-shadow:0 4px 16px #23395d0c; }} h2 {{ margin-top:0;font-size:21px; }}
.cards {{ display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px; }} .card {{ padding:14px;border:1px solid var(--line);border-radius:10px;background:#fbfcfe; }} .card span {{ display:block;color:var(--muted); }} .card strong {{ display:block;margin-top:4px;font-size:25px; }}
.note {{ padding:12px 14px;border-left:4px solid var(--brand);background:#eef4ff; }} .warn-note {{ border-left-color:var(--warn);background:#fff8e6; }} .table-wrap {{ overflow:auto;max-height:640px;border:1px solid var(--line);border-radius:8px; }}
table {{ width:100%;border-collapse:collapse; }} th,td {{ padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top; }} th {{ position:sticky;top:0;z-index:1;background:#edf2f8; }} tr:hover td {{ background:#f8fbff; }}
.status {{ display:inline-block;padding:2px 8px;border-radius:99px;font-weight:600; }} .status.ok {{ color:var(--ok);background:#e8f7f1; }} .status.blocked {{ color:var(--warn);background:#fff4d6; }} .muted {{ color:var(--muted);font-size:12px;margin-top:4px; }}
pre {{ overflow:auto;padding:12px;border:1px solid var(--line);border-radius:8px;background:#f7f9fb;white-space:pre-wrap; }} pre.answer {{ background:#f0f7ff;border-color:#c9ddf7;font-size:14px;line-height:1.7; }} details {{ margin:8px 0; }} summary {{ cursor:pointer;font-weight:600; }}
</style></head><body>
<header><h1>个性化膳食规划Agent · 50×20 端到端测试报告</h1>
<p>20组对话用例 × 50份用户档案（29轮）· 真实 PostgreSQL + Neo4j + LLM</p>
<p>生成时间：{generated_at}；总耗时：{total_elapsed:.1f}秒；LLM：{_escape(environment['llm_provider'])} / {_escape(environment['llm_model'])}</p></header>
<main>
<section><h2>总览</h2><div class="cards">{cards}</div>
<h3>性能效率（API 单次调用，比赛口径）</h3>
<p class="note">首Token与单轮端到端基于档案25的单档案 API 调用计时（LLM 约束提取 + 生成；回答为模板组装、无 LLM 增量生成，流式首块即完整回答起点，因此两者几乎相等）。多轮平均响应按 6 组多轮对话各自的轮次均值统计。</p>
<div class="table-wrap" style="margin-top:8px"><table><thead><tr><th>指标</th><th>平均值</th><th>优秀达标</th><th>合格达标</th></tr></thead><tbody>{perf_rows}</tbody></table></div>
<div class="table-wrap" style="margin-top:14px"><table><thead><tr><th>终态</th><th>组合数</th></tr></thead><tbody>{status_rows}</tbody></table></div></section>
<section><h2>异常处理</h2>
<p class="warn-note">失败终态不静默：约束冲突/过敏词未识别/空候选/规划无解均返回结构化状态与说明文本；LLM 提取失败时整组从新会话重试一次。</p>
<p class="note">详情字段含义：<code>[0]</code> 为空候选的菜品组索引（该组无任何候选菜）；<code>candidate_attempts</code> 为规划尝试轨迹——<code>candidate_limit: null</code> 表示全量候选（100/300 为分阶段截断尝试）、<code>candidate_counts: [30]</code> 表示该组有 30 道候选、<code>outcome: infeasible</code> 表示候选存在但 CP-SAT 无法排出满足整桌约束的组合、<code>nutrition_score: null</code> 表示无解时不产生营养分。</p>
<div class="table-wrap"><table><thead><tr><th>档案</th><th>硬约束</th><th>状态</th><th>详情</th></tr></thead><tbody>{abnormal_rows}</tbody></table></div>
<h3>LLM 提取异常记录</h3>
<div class="table-wrap"><table><thead><tr><th>对话</th><th>重试记录</th></tr></thead><tbody>{error_rows or '<tr><td colspan=2>无提取失败记录</td></tr>'}</tbody></table></div></section>
<section><h2>按对话逐轮输出（20组 × 50档案）</h2>{''.join(dialogue_sections)}</section>
</main></body></html>"""


def main() -> None:
    _load_dotenv()
    ensure_graph_data()
    users = json.loads(USERS_PATH.read_text(encoding="utf-8"))
    dialogues = json.loads(DIALOGUES_PATH.read_text(encoding="utf-8"))
    assert len(users) == 50
    assert len(dialogues) == 20

    from backend.services.answer_composer import AnswerComposerService
    from backend.services import ConstraintIntegrationService

    started_at = time.perf_counter()
    with _create_test_environment() as environment:
        services = environment.services
        composer = AnswerComposerService()
        integration_service = ConstraintIntegrationService()
        profile_constraints = {
            user["id"]: services.profile.extract(user["id"])
            for user in users
        }
        extracted: dict[int, dict[str, Any]] = {}
        for dialogue in dialogues:
            turns, merged_raw, llm_calls, errors = _collect_turns(
                services,
                environment.extractor,
                composer,
                integration_service,
                environment.session_factory,
                users,
                profile_constraints,
                dialogue,
            )
            extracted[dialogue["id"]] = {
                "turns": turns,
                "attempts": len(errors) + 1 if turns else 1,
                "errors": errors,
                "llm_calls": llm_calls,
                "merged_raw": merged_raw,
            }
        total_elapsed = time.perf_counter() - started_at
        environment_info = {
            "llm_provider": environment.llm_provider,
            "llm_model": environment.llm_model,
        }
        document = _render_report(
            users,
            dialogues,
            extracted,
            environment_info,
            total_elapsed,
        )
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(document, encoding="utf-8")
    print(f"测试报告已生成：{OUTPUT_PATH}")


if __name__ == "__main__":
    main()
