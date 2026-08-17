from __future__ import annotations

import html
import json
import os
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]
USERS_PATH = (
    REPO_ROOT
    / "datas"
    / "processed"
    / "users"
    / "50个用户健康档案_归一化.json"
)
RECIPES_PATH = REPO_ROOT / "datas" / "processed" / "Recipes" / "RecipeComplete.json"
INGREDIENTS_PATH = (
    REPO_ROOT / "datas" / "processed" / "Ingredients" / "Ingredients2Nutrition.csv"
)
PROFILES_PATH = USERS_PATH
DRI_PATH = REPO_ROOT / "datas" / "processed" / "Nutrition" / "DRI2023.csv"
DIALOGUES_PATH = REPO_ROOT / "datas" / "raw" / "对话用例.json"
REPORT_PATH = (
    REPO_ROOT / "docs" / "spec_08" / "Spec_08_50x6端到端业务报告.html"
)
CASES_DATA_PATH = (
    REPO_ROOT / "tests" / ".pytest-tmp" / "spec08_50x6_cases.json"
)

EXPECTED_PROFILE_COUNT = 50
EXPECTED_DIALOGUE_COUNT = 6
EXPECTED_RECIPE_COUNT = 1912
EXPECTED_LLM_MODEL = "qwen3.7-flash"
MAX_WORKERS = 8
FIXED_CLOCK_HOUR = 12
FIXED_CLOCK_MINUTE = 0
LLM_ENVIRONMENT_NAMES = frozenset(
    {
        "LLM_PROVIDER",
        "LLM_BASE_URL",
        "LLM_AUTH_TOKEN",
        "LLM_MODEL",
    }
)


@dataclass
class CaseResult:
    """一场多轮会话的端到端业务结果。"""

    profile_id: int
    dialogue_id: int
    completed_turns: int
    expected_turns: int
    llm_calls: int
    status: str
    final_status: str
    meal_periods: list[str]
    diner_count: int | None
    total_dish_count: int | None
    max_difficulty: str | None
    dish_count: int
    missing_requirements: list[str]
    integration_status: str
    conflict_count: int
    elapsed_seconds: float
    detail: str
    turns: list[dict[str, Any]]


def _fixed_clock() -> Any:
    """返回固定上海本地时间(午餐时段)的时钟,保证判定不依赖运行时刻。"""

    def clock() -> datetime:
        return datetime(2026, 8, 14, FIXED_CLOCK_HOUR, FIXED_CLOCK_MINUTE)

    return clock


def _load_dotenv(env_path: Path | None = None) -> None:
    """加载环境;LLM配置以.env为准,其他配置保留进程优先级。"""

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
    import tomllib

    with (REPO_ROOT / "pyproject.toml").open("rb") as stream:
        project_config = tomllib.load(stream)
    database_url = project_config["tool"]["mealagent"]["test_database"]["url"]
    required_database = project_config["tool"]["mealagent"]["test_database"][
        "required_database"
    ]
    parsed_url = make_url(database_url)
    if not parsed_url.drivername.startswith("postgresql"):
        raise pytest.UsageError("完整 Spec_08 必须使用 PostgreSQL 测试库")
    if parsed_url.database != required_database:
        raise pytest.UsageError(
            f"测试只允许连接 {required_database}"
        )
    return database_url


class CountingLLMClient:
    """包装真实提取器并统计实际LLM调用次数,记录每轮原始输出。"""

    def __init__(self, extractor: object) -> None:
        self._extractor = extractor
        self.calls = 0
        self.responses: list[object] = []

    def __call__(self, prompt: str) -> object:
        self.calls += 1
        result = self._extractor(prompt)
        self.responses.append(result)
        return result


def _run_case(
    *,
    profile_id: int,
    dialogue: dict[str, Any],
    profile_constraints: dict[str, Any],
    session_factory: Any,
    integration_service: Any,
) -> CaseResult:
    """运行一场多轮会话,逐轮真实LLM提取,结束后走 Spec_03 整合。"""

    started_at = time.perf_counter()
    dialogue_id = dialogue["id"]
    expected_turns = len(dialogue["user_messages"])

    from backend.infrastructure.llm import (
        create_langchain_multi_turn_extractor_from_environment,
    )
    from backend.services.meal_period_resolution import (
        MealPeriodResolutionService,
    )
    from backend.services.multi_turn_constraints import (
        MultiTurnConstraintService,
    )

    llm_client = CountingLLMClient(
        create_langchain_multi_turn_extractor_from_environment()
    )
    service = MultiTurnConstraintService(
        session_factory,
        llm_client,
        MealPeriodResolutionService(clock=_fixed_clock()),
    )

    completed_turns = 0
    final_status = ""
    merged: dict[str, Any] | None = None
    missing: list[str] = []
    failure_details: list[str] = []
    turn_records: list[dict[str, Any]] = []

    try:
        session_id = service.create_session(profile_id)
    except Exception as exc:
        return CaseResult(
            profile_id=profile_id,
            dialogue_id=dialogue_id,
            completed_turns=0,
            expected_turns=expected_turns,
            llm_calls=llm_client.calls,
            status="session_failed",
            final_status="",
            meal_periods=[],
            diner_count=None,
            total_dish_count=None,
            max_difficulty=None,
            dish_count=0,
            missing_requirements=[],
            integration_status="未整合",
            conflict_count=0,
            elapsed_seconds=time.perf_counter() - started_at,
            detail=f"会话创建失败：{type(exc).__name__}：{exc}",
            turns=[],
        )

    for message in dialogue["user_messages"]:
        turn_number = completed_turns + 1
        responses_before = len(llm_client.responses)
        try:
            result = service.submit_turn(session_id, message)
            completed_turns += 1
            final_status = result["status"]
            missing = result["missing_requirements"]
            merged = result["merged_constraints"]
            turn_records.append(
                {
                    "turn_number": turn_number,
                    "message": message,
                    "llm_calls": len(llm_client.responses) - responses_before,
                    "ok": True,
                    "output": llm_client.responses[-1],
                    "session_status": final_status,
                    "missing_requirements": missing,
                    "error": "",
                }
            )
        except Exception as exc:
            failure_details.append(
                f"第{turn_number}轮失败：{type(exc).__name__}：{exc}"
            )
            turn_records.append(
                {
                    "turn_number": turn_number,
                    "message": message,
                    "llm_calls": len(llm_client.responses) - responses_before,
                    "ok": False,
                    "output": (
                        llm_client.responses[-1]
                        if len(llm_client.responses) > responses_before
                        else None
                    ),
                    "session_status": final_status,
                    "missing_requirements": missing,
                    "error": str(exc),
                }
            )
            break

    integration_status = "未整合"
    conflict_count = 0
    if merged is not None:
        try:
            integrated = integration_service.integrate(
                profile_constraints,
                merged,
            )
            if integrated["has_conflicts"]:
                integration_status = "冲突"
                conflict_count = len(integrated["conflicts"])
            else:
                integration_status = "通过"
        except Exception as exc:
            integration_status = "整合失败"
            failure_details.append(
                f"整合失败：{type(exc).__name__}：{exc}"
            )

    if completed_turns == 0:
        status = "session_failed"
    elif completed_turns < expected_turns:
        status = "turn_failed"
    else:
        status = "completed"

    return CaseResult(
        profile_id=profile_id,
        dialogue_id=dialogue_id,
        completed_turns=completed_turns,
        expected_turns=expected_turns,
        llm_calls=llm_client.calls,
        status=status,
        final_status=final_status,
        meal_periods=list(merged["meal_periods"]) if merged else [],
        diner_count=merged["diner_count"] if merged else None,
        total_dish_count=(
            merged["total_dish_count"] if merged else None
        ),
        max_difficulty=merged["max_difficulty"] if merged else None,
        dish_count=len(merged["dishes"]) if merged else 0,
        missing_requirements=missing,
        integration_status=integration_status,
        conflict_count=conflict_count,
        elapsed_seconds=time.perf_counter() - started_at,
        detail="；".join(failure_details),
        turns=turn_records,
    )


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _status_label(status: str) -> str:
    labels = {
        "completed": "全部轮次成功",
        "turn_failed": "轮次部分失败",
        "session_failed": "会话失败",
    }
    return labels.get(status, status)


def _final_status_label(final_status: str) -> str:
    labels = {
        "ready_for_planning": "可规划",
        "needs_confirmation": "餐次待确认",
    }
    return labels.get(final_status, final_status or "-")


def _generate_report(
    *,
    users: list[dict[str, Any]],
    dialogues: list[dict[str, Any]],
    cases: list[CaseResult],
    environment: dict[str, Any],
    total_elapsed: float,
    clock_text: str,
) -> None:
    """输出不依赖外部资源的端到端业务报告。"""

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    counts = Counter(case.status for case in cases)
    by_dialogue: dict[int, list[CaseResult]] = defaultdict(list)
    by_profile: dict[int, list[CaseResult]] = defaultdict(list)
    for case in cases:
        by_dialogue[case.dialogue_id].append(case)
        by_profile[case.profile_id].append(case)

    total_llm_calls = sum(case.llm_calls for case in cases)
    total_turns = sum(case.expected_turns for case in cases)
    completed_turns = sum(case.completed_turns for case in cases)
    final_status_counts = Counter(case.final_status for case in cases)
    integration_counts = Counter(
        case.integration_status for case in cases
    )

    dialogue_rows = []
    for dialogue in dialogues:
        dialogue_id = dialogue["id"]
        rows = by_dialogue[dialogue_id]
        status_counts = Counter(row.status for row in rows)
        dialogue_rows.append(
            "<tr>"
            f"<td>{dialogue_id}</td>"
            f"<td>{_escape(dialogue['user_messages'][0])}</td>"
            f"<td>{rows[0].expected_turns}</td>"
            f"<td>{sum(r.completed_turns for r in rows)}/{sum(r.expected_turns for r in rows)}</td>"
            f"<td>{sum(r.llm_calls for r in rows)}</td>"
            f"<td>{sum(r.elapsed_seconds for r in rows) / len(rows):.3f}s</td>"
            f"<td>{_escape(', '.join(f'{_status_label(k)}={v}' for k, v in status_counts.items()))}</td>"
            "</tr>"
        )

    user_by_id = {user["id"]: user for user in users}
    profile_rows = []
    for profile_id in sorted(by_profile):
        user = user_by_id[profile_id]
        rows = by_profile[profile_id]
        status_counts = Counter(row.status for row in rows)
        profile_conflicts = sum(row.conflict_count for row in rows)
        profile_rows.append(
            "<tr>"
            f"<td>{profile_id}</td>"
            f"<td>{_escape(user['性别'])} / {_escape(user['年龄'])}</td>"
            f"<td>{_escape(user.get('特殊人群', []))}</td>"
            f"<td>{status_counts.get('completed', 0)}</td>"
            f"<td>{status_counts.get('turn_failed', 0)}</td>"
            f"<td>{status_counts.get('session_failed', 0)}</td>"
            f"<td>{sum(r.elapsed_seconds for r in rows) / len(rows):.3f}s</td>"
            f"<td>{profile_conflicts}</td>"
            "</tr>"
        )

    detail_rows = []
    for case in cases:
        css_class = "ok" if case.status == "completed" else "fail"
        detail_rows.append(
            "<tr>"
            f"<td>{case.profile_id}</td>"
            f"<td>{case.dialogue_id}</td>"
            f"<td>{case.completed_turns}/{case.expected_turns}</td>"
            f"<td>{case.llm_calls}</td>"
            f'<td><span class="status {css_class}">{_escape(_status_label(case.status))}</span></td>'
            f"<td>{_escape(_final_status_label(case.final_status))}</td>"
            f"<td>{_escape(case.meal_periods)}</td>"
            f"<td>{_escape(case.diner_count if case.diner_count is not None else '-')}</td>"
            f"<td>{_escape(case.total_dish_count if case.total_dish_count is not None else '-')}</td>"
            f"<td>{_escape(case.max_difficulty or '-')}</td>"
            f"<td>{case.dish_count}</td>"
            f"<td>{_escape(case.missing_requirements)}</td>"
            f'<td><span class="status {"ok" if case.integration_status == "通过" else ("blocked" if case.integration_status == "冲突" else "fail")}">{_escape(case.integration_status)}</span></td>'
            f"<td>{case.conflict_count}</td>"
            f"<td>{case.elapsed_seconds:.3f}s</td>"
            f"<td>{_escape(case.detail or '-')}</td>"
            "</tr>"
        )

    report_data = json.dumps(
        [asdict(case) for case in cases],
        ensure_ascii=False,
        default=str,
    ).replace("</", "<\\/")

    # 会话内逐轮业务流程明细:档案摘要 + 每轮原文、LLM输出与状态
    flow_blocks = []
    for case in cases:
        user = user_by_id[case.profile_id]
        profile_summary = (
            f"档案{case.profile_id}：{_escape(user['性别'])} / "
            f"{_escape(user['年龄'])}岁；特殊人群 "
            f"{_escape(user.get('特殊人群', []))}；过敏 "
            f"{_escape(user.get('过敏食材', []))}"
        )
        turn_blocks = []
        for turn in case.turns:
            turn_css = "ok" if turn["ok"] else "fail"
            output_text = json.dumps(
                turn["output"], ensure_ascii=False, indent=2, default=str
            ) if turn["output"] is not None else "无输出"
            if turn["ok"]:
                result_block = (
                    f'<p>会话状态：<span class="status ok">{_escape(_final_status_label(turn["session_status"]))}</span>'
                    f"；缺失要素：{_escape(turn['missing_requirements'])}</p>"
                )
            else:
                result_block = (
                    f'<p class="status fail">本轮失败</p>'
                    f"<p>错误：{_escape(turn['error'])}</p>"
                )
            turn_blocks.append(
                "<div>"
                f"<h4>第{turn['turn_number']}轮（LLM调用 {turn['llm_calls']} 次）"
                f'<span class="status {turn_css}">{"成功" if turn["ok"] else "失败"}</span></h4>'
                f"<p>用户原文：<code>{_escape(turn['message'])}</code></p>"
                f"{result_block}"
                f"<pre>{_escape(output_text)}</pre>"
                "</div>"
            )
        flow_blocks.append(
            "<details>"
            f"<summary>{_escape(profile_summary)} × 对话{case.dialogue_id}："
            f"{_escape(_status_label(case.status))} · "
            f"{_escape(_final_status_label(case.final_status))} · "
            f"整合{_escape(case.integration_status)}</summary>"
            + "".join(turn_blocks)
            + "</details>"
        )

    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Spec_08 50×6 端到端业务报告</title>
  <style>
    :root {{ color-scheme: light; --ink:#17202a; --muted:#65717e; --line:#dce3ea; --brand:#155eef; --ok:#087a55; --warn:#9a6700; --fail:#c62828; }}
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
    .muted {{ color:var(--muted); }}
  </style>
</head>
<body>
<header>
  <h1>Spec_08 多轮约束提取：50份档案 × 6组多轮对话</h1>
  <p>真实 PostgreSQL + 真实 LLM（逐轮结构化提取与重放校验）+ Spec_03 约束整合验证</p>
  <p>生成时间：{_escape(generated_at)}；总耗时：{total_elapsed:.3f} 秒</p>
</header>
<main>
  <section>
    <h2>执行口径</h2>
    <p class="note">6组多轮对话（用例15-20，2-4轮）每轮都调用真实LLM结合当前约束状态提取，代码层完成结构校验、变更声明重放校验与证据子串校验，失败重试一次；50份档案各自独立会话，共300场会话、750轮次。餐次完整性判定使用固定时钟 {_escape(clock_text)}（Asia/Shanghai），每场会话结束后用 Spec_03 与对应档案约束做整合验证。</p>
    <ul>
      <li>数据：50份归一化健康档案、1912道菜谱、1245种归一化食材（真实业务数据库）。</li>
      <li>LLM：{_escape(environment.get('llm_provider', '-'))} / {_escape(environment.get('llm_model', '-'))}，每轮最多2次调用（502重试一次）。</li>
      <li>并行：8线程执行300场会话，每线程独立LLM提取器与独立数据库Session。</li>
      <li>终态：全部轮次成功 / 轮次部分失败 / 会话失败；会话终态为可规划(ready_for_planning)或餐次待确认(needs_confirmation)。</li>
      <li>整合：会话有合并结果即与档案约束整合，记录通过、过敏冲突或整合失败。</li>
    </ul>
  </section>
  <section>
    <h2>结果总览</h2>
    <div class="cards">
      <div class="card"><span>总会话</span><strong>{len(cases)}</strong></div>
      <div class="card"><span>全部轮次成功</span><strong>{counts.get('completed', 0)}</strong></div>
      <div class="card"><span>轮次部分失败</span><strong>{counts.get('turn_failed', 0)}</strong></div>
      <div class="card"><span>会话失败</span><strong>{counts.get('session_failed', 0)}</strong></div>
      <div class="card"><span>可规划终态</span><strong>{final_status_counts.get('ready_for_planning', 0)}</strong></div>
      <div class="card"><span>餐次待确认</span><strong>{final_status_counts.get('needs_confirmation', 0)}</strong></div>
      <div class="card"><span>完成轮次</span><strong>{completed_turns}/{total_turns}</strong></div>
      <div class="card"><span>LLM总调用</span><strong>{total_llm_calls}</strong></div>
      <div class="card"><span>整合通过</span><strong>{integration_counts.get('通过', 0)}</strong></div>
      <div class="card"><span>整合冲突</span><strong>{integration_counts.get('冲突', 0)}</strong></div>
      <div class="card"><span>整合失败</span><strong>{integration_counts.get('整合失败', 0)}</strong></div>
      <div class="card"><span>未整合</span><strong>{integration_counts.get('未整合', 0)}</strong></div>
    </div>
  </section>
  <section>
    <h2>按对话汇总</h2>
    <div class="table-wrap"><table><thead><tr>
      <th>ID</th><th>首句原文</th><th>轮数</th><th>完成轮次</th><th>LLM调用</th>
      <th>平均会话耗时</th><th>终态分布</th>
    </tr></thead><tbody>
    {"".join(dialogue_rows)}
    </tbody></table></div>
  </section>
  <section>
    <h2>按用户档案汇总</h2>
    <div class="table-wrap"><table><thead><tr>
      <th>档案ID</th><th>性别 / 年龄</th><th>特殊人群</th><th>全部轮次成功</th>
      <th>轮次部分失败</th><th>会话失败</th><th>平均会话耗时</th><th>整合冲突数</th>
    </tr></thead><tbody>
    {"".join(profile_rows)}
    </tbody></table></div>
  </section>
  <section>
    <h2>300场会话明细</h2>
    <div class="table-wrap"><table><thead><tr>
      <th>档案</th><th>对话</th><th>完成轮次</th><th>LLM调用</th><th>会话状态</th>
      <th>会话终态</th><th>餐次</th><th>人数</th><th>整桌总数</th><th>难度上限</th>
      <th>Dish数</th><th>缺失要素</th>
      <th>整合</th><th>冲突数</th><th>耗时</th><th>详情</th>
    </tr></thead><tbody>
    {"".join(detail_rows)}
    </tbody></table></div>
  </section>
  <section>
    <h2>业务流程明细（300场会话逐轮）</h2>
    <p class="muted">每场会话按轮次展开:档案摘要 → 本轮用户原文 → LLM原始输出(含变更声明change_actions与证据evidence)→ 会话状态与缺失要素。</p>
    <style>details {{ border:1px solid var(--line); border-radius:8px; margin:8px 0; padding:8px 14px; background:#fbfcfe; }} summary {{ cursor:pointer; font-weight:600; }} h4 {{ margin:14px 0 4px; }} pre {{ background:#f2f5f9; padding:12px; border-radius:8px; overflow:auto; white-space:pre-wrap; font-size:12px; }}</style>
    {"".join(flow_blocks)}
  </section>
  <section>
    <h2>验收结论</h2>
    <ul>
      <li>执行范围：50份档案 × 6组多轮对话 = 300场会话、750轮次。</li>
      <li>会话失败：{counts.get('session_failed', 0)}场。</li>
      <li>全部轮次成功：{counts.get('completed', 0)}场（{counts.get('completed', 0) / len(cases) * 100:.1f}%），完成轮次 {completed_turns}/{total_turns}。</li>
      <li>LLM总调用 {total_llm_calls} 次（含502重试）。</li>
      <li>终态：可规划 {final_status_counts.get('ready_for_planning', 0)}、餐次待确认 {final_status_counts.get('needs_confirmation', 0)}。</li>
      <li>整合：通过 {integration_counts.get('通过', 0)}、冲突 {integration_counts.get('冲突', 0)}、失败 {integration_counts.get('整合失败', 0)}、未整合 {integration_counts.get('未整合', 0)}。</li>
    </ul>
  </section>
</main>
<script type="application/json" id="case-data">{report_data}</script>
</body>
</html>
"""
    REPORT_PATH.write_text(document, encoding="utf-8")


@pytest.mark.integration
def test_50份真实档案与6组多轮对话贯通约束整合() -> None:
    """运行300场多轮会话,验证真实数据和真实LLM贯通到 Spec_03 整合。"""

    _load_dotenv()
    assert os.environ.get("LLM_MODEL") == EXPECTED_LLM_MODEL, (
        "真实端到端测试必须使用.env中的qwen3.7-flash，实际为："
        f"{os.environ.get('LLM_MODEL')}"
    )

    from backend.infrastructure.database import create_session_factory
    from backend.infrastructure.database.importer import import_basic_data
    from backend.infrastructure.database.models import (
        Base,
        Recipe,
        UserProfile,
    )
    from backend.services import (
        ConstraintIntegrationService,
        ProfileConstraintService,
    )

    users = _load_json_array(USERS_PATH)
    dialogues = [
        item
        for item in _load_json_array(DIALOGUES_PATH)
        if item.get("turn_count", 1) >= 2
    ]
    assert len(users) == EXPECTED_PROFILE_COUNT
    assert len(dialogues) == EXPECTED_DIALOGUE_COUNT
    assert len({user["id"] for user in users}) == EXPECTED_PROFILE_COUNT
    assert len({item["id"] for item in dialogues}) == EXPECTED_DIALOGUE_COUNT

    engine = create_engine(_load_test_database_url(), pool_pre_ping=True)
    try:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            import_basic_data(
                RECIPES_PATH,
                INGREDIENTS_PATH,
                PROFILES_PATH,
                DRI_PATH,
                session,
            )

        session_factory = create_session_factory(engine)
        with session_factory() as session:
            postgres_counts = {
                "profiles": session.scalar(select(func.count(UserProfile.id))),
                "postgres_recipes": session.scalar(
                    select(func.count(Recipe.id))
                ),
            }
        assert postgres_counts == {
            "profiles": EXPECTED_PROFILE_COUNT,
            "postgres_recipes": EXPECTED_RECIPE_COUNT,
        }

        started_at = time.perf_counter()
        cases: list[CaseResult] = []
        profile_constraints: dict[int, dict[str, Any]] = {}
        profile_errors: dict[int, str] = {}

        profile_service = ProfileConstraintService(session_factory)
        for user in users:
            profile_id = user["id"]
            try:
                profile_constraints[profile_id] = profile_service.extract(
                    profile_id
                )
            except Exception as exc:
                profile_errors[profile_id] = f"{type(exc).__name__}：{exc}"

        integration_service = ConstraintIntegrationService()
        case_tasks: list[dict[str, Any]] = []
        for user in users:
            profile_id = user["id"]
            for dialogue in dialogues:
                case_tasks.append(
                    {
                        "profile_id": profile_id,
                        "dialogue": dialogue,
                        "profile_constraints": profile_constraints.get(
                            profile_id
                        ),
                        "session_factory": session_factory,
                        "integration_service": integration_service,
                    }
                )

        # 会话间相互独立,并行执行(每轮真实LLM调用占主要耗时)
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            cases = list(pool.map(lambda task: _run_case(**task), case_tasks))

        total_elapsed = time.perf_counter() - started_at
        environment = {
            **postgres_counts,
            "llm_provider": os.environ.get("LLM_PROVIDER", "anthropic"),
            "llm_model": os.environ.get("LLM_MODEL", "未配置"),
            "enable_thinking": os.environ.get(
                "LLM_ENABLE_THINKING", "false"
            ),
        }
        # 先落盘结果数据:报告生成或后续断言出错时,数据仍可复用
        CASES_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        CASES_DATA_PATH.write_text(
            json.dumps(
                {
                    "users": users,
                    "dialogues": dialogues,
                    "cases": [asdict(case) for case in cases],
                    "environment": environment,
                    "total_elapsed": total_elapsed,
                    "clock_text": (
                        f"{FIXED_CLOCK_HOUR:02d}:{FIXED_CLOCK_MINUTE:02d}"
                    ),
                },
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )
        _generate_report(
            users=users,
            dialogues=dialogues,
            cases=cases,
            environment=environment,
            total_elapsed=total_elapsed,
            clock_text=f"{FIXED_CLOCK_HOUR:02d}:{FIXED_CLOCK_MINUTE:02d}",
        )

        assert len(cases) == EXPECTED_PROFILE_COUNT * EXPECTED_DIALOGUE_COUNT
        assert not profile_errors, f"档案约束提取失败:{profile_errors}"
        session_failures = [
            case for case in cases if case.status == "session_failed"
        ]
        assert not session_failures, (
            "存在会话失败:"
            + json.dumps(
                [asdict(case) for case in session_failures],
                ensure_ascii=False,
                default=str,
            )
        )
        incomplete_cases = [
            case for case in cases if case.status != "completed"
        ]
        assert not incomplete_cases, (
            "存在未完成全部轮次的会话:"
            + json.dumps(
                [asdict(case) for case in incomplete_cases],
                ensure_ascii=False,
                default=str,
            )
        )
        integration_failures = [
            case
            for case in cases
            if case.integration_status not in {"通过", "冲突"}
        ]
        assert not integration_failures, (
            "存在约束整合失败:"
            + json.dumps(
                [asdict(case) for case in integration_failures],
                ensure_ascii=False,
                default=str,
            )
        )

        for case in cases:
            final_output = case.turns[-1]["output"]
            context = json.dumps(
                {
                    "profile_id": case.profile_id,
                    "dialogue_id": case.dialogue_id,
                    "input": case.turns[-1]["message"],
                    "output": final_output,
                },
                ensure_ascii=False,
                default=str,
            )
            assert isinstance(final_output, dict), context
            if case.dialogue_id == 18:
                assert final_output["max_difficulty"] == "中等", context
            elif case.dialogue_id == 19:
                assert final_output["max_difficulty"] == "简单", context
            elif case.dialogue_id == 20:
                assert final_output["total_dish_count"] is None, context
                assert final_output["max_difficulty"] == "中等", context
                assert len(final_output["dishes"]) == 2, context
                assert [
                    dish["count"] for dish in final_output["dishes"]
                ] == [None, None], context
                assert {
                    dish["taste_preferences"].get("is_spicy")
                    for dish in final_output["dishes"]
                } == {True, False}, context
    finally:
        engine.dispose()
