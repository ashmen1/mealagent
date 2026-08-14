from __future__ import annotations

import html
import json
import os
import random
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
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
REPORT_PATH = (
    REPO_ROOT / "docs" / "spec_06" / "Spec_06_50x14端到端业务报告.html"
)
CASES_DATA_PATH = (
    REPO_ROOT / "tests" / ".pytest-tmp" / "spec06_50x14_cases.json"
)
SUPPORTED_MEAL_PERIODS = frozenset({"早餐", "午餐", "晚餐"})
CANDIDATE_LIMIT_PER_DISH = 100
CANDIDATE_RANDOM_SEED = 42
EXPECTED_PROFILE_COUNT = 50
EXPECTED_DIALOGUE_COUNT = 14
EXPECTED_LLM_MODEL = "qwen3.7-flash"
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
    """一组档案与对话的端到端业务结果。"""

    profile_id: int
    dialogue_id: int
    status: str
    meal_window: str
    meal_period: str
    diner_count: int | None
    special_populations: list[str]
    allergens: list[str]
    dish_count: int
    candidate_counts: list[int]
    used_candidate_counts: list[int]
    sampling_seeds: list[int]
    selected_recipes: list[str]
    nutrition_score: int | None
    elapsed_seconds: float
    detail: str


def _fixed_clock(hour: int, minute: int):
    """返回指定上海本地时间的固定时钟（Spec_07 时间段测试用）。"""

    def clock() -> datetime:
        return datetime(2026, 8, 14, hour, minute)

    return clock


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
    """读取对象数组并在测试入口明确验证数据形状。"""
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, list), f"{path.name}顶层必须是数组"
    assert all(isinstance(item, dict) for item in loaded), (
        f"{path.name}只能包含对象"
    )
    return loaded


def _build_menu_input(
    *,
    profile_constraints: dict[str, Any],
    integrated: dict[str, Any],
    filtering_result: dict[str, Any],
    nutrition_service: Any,
    meal_period: str,
) -> tuple[dict[str, Any], list[int], list[int], list[int]]:
    """把真实过滤结果、菜谱营养和单餐目标整合为菜单规划输入。"""

    candidate_counts = [
        len(candidates) for candidates in filtering_result["dishes"]
    ]
    limited_groups = []
    sampling_seeds = []
    for dish_index, candidates in enumerate(filtering_result["dishes"]):
        sampled, seed = _sample_candidate_group(
            candidates,
            profile_id=integrated["profile_id"],
            dialogue_id=integrated["dialogue_id"],
            dish_index=dish_index,
        )
        limited_groups.append(sampled)
        sampling_seeds.append(seed)
    used_candidate_counts = [len(candidates) for candidates in limited_groups]
    recipe_names = list(
        dict.fromkeys(
            candidate["recipe_name"]
            for candidates in limited_groups
            for candidate in candidates
        )
    )
    assert "果蔬清洗" not in recipe_names, "无效菜谱果蔬清洗仍在候选中"
    nutrition_by_name: dict[str, dict[str, Any]] = {}
    if recipe_names:
        recipe_nutrition = nutrition_service.get_recipe_nutrition(recipe_names)
        nutrition_by_name = {
            item["recipe_name"]: item for item in recipe_nutrition
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
                            "energy_kcal",
                            "protein_g",
                            "fat_g",
                            "carbohydrate_g",
                            "fiber_g",
                            "sodium_mg",
                            "calcium_mg",
                            "iron_mg",
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
        used_candidate_counts,
        sampling_seeds,
    )


def _sample_candidate_group(
    candidates: list[dict[str, Any]],
    *,
    profile_id: int,
    dialogue_id: int,
    dish_index: int,
) -> tuple[list[dict[str, Any]], int]:
    """按稳定种子随机抽样，并恢复候选原始顺序。"""

    seed = (
        CANDIDATE_RANDOM_SEED * 1_000_000
        + profile_id * 10_000
        + dialogue_id * 100
        + dish_index
    )
    if len(candidates) <= CANDIDATE_LIMIT_PER_DISH:
        return list(candidates), seed
    sampled_indexes = sorted(
        random.Random(seed).sample(
            range(len(candidates)), CANDIDATE_LIMIT_PER_DISH
        )
    )
    return [candidates[index] for index in sampled_indexes], seed


def _assert_planned_result(
    result: dict[str, Any],
    planning_input: dict[str, Any],
) -> None:
    """检查完整链路成功结果的关键跨模块不变量。"""

    selected = result["selected_dishes"]
    selected_names = [item["recipe_name"] for item in selected]
    assert selected
    assert len(selected_names) == len(set(selected_names))
    assert result["profile_id"] == planning_input["profile_id"]
    assert result["dialogue_id"] == planning_input["dialogue_id"]
    assert result["meal_period"] == planning_input["meal_period"]
    assert result["diner_count"] == (planning_input["diner_count"] or 1)
    assert 0 <= result["nutrition_score"] <= 16

    calculated_totals = {
        nutrient: sum(
            (item["nutrition"][nutrient] for item in selected),
            Decimal("0"),
        )
        for nutrient in result["total_nutrition"]
    }
    assert result["total_nutrition"] == calculated_totals

    diners = result["diner_count"]
    targets = planning_input["nutrient_targets"]
    if "高血压" in planning_input["special_populations"]:
        assert result["total_nutrition"]["sodium_mg"] <= (
            targets["sodium_mg"]["upper_bound"] * diners
        )


def _new_case_result(
    *,
    profile_id: int,
    dialogue_id: int,
    status: str,
    meal_window: str,
    profile_constraints: dict[str, Any] | None,
    integrated: dict[str, Any] | None,
    started_at: float,
    detail: str,
    meal_period: str = "",
    candidate_counts: list[int] | None = None,
    used_candidate_counts: list[int] | None = None,
    sampling_seeds: list[int] | None = None,
    selected_recipes: list[str] | None = None,
    nutrition_score: int | None = None,
) -> CaseResult:
    """统一创建报告行，避免异常路径缺失字段。"""

    return CaseResult(
        profile_id=profile_id,
        dialogue_id=dialogue_id,
        status=status,
        meal_window=meal_window,
        meal_period=meal_period,
        diner_count=(integrated or {}).get("diner_count"),
        special_populations=list(
            (profile_constraints or {}).get("special_populations", [])
        ),
        allergens=list((profile_constraints or {}).get("allergens", [])),
        dish_count=len((integrated or {}).get("dishes", [])),
        candidate_counts=list(candidate_counts or []),
        used_candidate_counts=list(used_candidate_counts or []),
        sampling_seeds=list(sampling_seeds or []),
        selected_recipes=list(selected_recipes or []),
        nutrition_score=nutrition_score,
        elapsed_seconds=round(time.perf_counter() - started_at, 4),
        detail=detail,
    )


def _run_case(
    *,
    profile_id: int,
    dialogue_id: int,
    meal_window: str,
    profile_constraints: dict[str, Any] | None,
    dialogue_constraints: dict[str, Any] | None,
    profile_error: str | None,
    dialogue_error: str | None,
    meal_period_service: Any,
    services: Any,
    integration_service: Any,
    nutrition_service: Any,
    menu_service: Any,
    menu_error_type: type[Exception],
) -> CaseResult:
    """运行一组档案与对话，保留所有业务终态和技术失败。"""

    started_at = time.perf_counter()
    if profile_error is not None:
        return _new_case_result(
            profile_id=profile_id,
            dialogue_id=dialogue_id,
            meal_window=meal_window,
            status="technical_failure",
            profile_constraints=None,
            integrated=None,
            started_at=started_at,
            detail=f"档案约束提取失败：{profile_error}",
        )
    if dialogue_error is not None:
        return _new_case_result(
            profile_id=profile_id,
            dialogue_id=dialogue_id,
            meal_window=meal_window,
            status="technical_failure",
            profile_constraints=profile_constraints,
            integrated=None,
            started_at=started_at,
            detail=f"对话约束提取失败：{dialogue_error}",
        )

    integrated: dict[str, Any] | None = None
    candidate_counts: list[int] = []
    used_candidate_counts: list[int] = []
    sampling_seeds: list[int] = []
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
                meal_window=meal_window,
                status="constraint_conflict",
                profile_constraints=profile_constraints,
                integrated=integrated,
                started_at=started_at,
                detail=json.dumps(
                    integrated["conflicts"], ensure_ascii=False
                ),
            )

        meal_periods = integrated["meal_periods"]
        resolution_note = ""
        if (
            len(meal_periods) != 1
            or meal_periods[0] not in SUPPORTED_MEAL_PERIODS
        ):
            # Spec_07 餐次解析：未明确餐次时按上海当前时间解析，
            # 无法确定才记为餐次待确认
            resolution = meal_period_service.resolve(meal_periods)
            if resolution["status"] == "needs_confirmation":
                return _new_case_result(
                    profile_id=profile_id,
                    dialogue_id=dialogue_id,
                    meal_window=meal_window,
                    status="meal_period_blocked",
                    profile_constraints=profile_constraints,
                    integrated=integrated,
                    started_at=started_at,
                    detail=(
                        "菜单规划只接受一个早餐、午餐或晚餐，实际为："
                        + json.dumps(meal_periods, ensure_ascii=False)
                        + f"；餐次解析待确认（{resolution['reason']}）"
                    ),
                )
            meal_period = resolution["meal_period"]
            resolution_note = f"（餐次按{resolution['source']}解析）"
        else:
            meal_period = meal_periods[0]

        filtering_result = services.dish_filtering.filter(integrated)
        (
            planning_input,
            candidate_counts,
            used_candidate_counts,
            sampling_seeds,
        ) = (
            _build_menu_input(
                profile_constraints=profile_constraints,
                integrated=integrated,
                filtering_result=filtering_result,
                nutrition_service=nutrition_service,
                meal_period=meal_period,
            )
        )
        try:
            result = menu_service.plan(planning_input)
        except menu_error_type as exc:
            status_code = getattr(exc, "status_code", None)
            if status_code == 422:
                if filtering_result["unmatched_allergens"]:
                    status = "allergen_blocked"
                elif any(count == 0 for count in candidate_counts):
                    status = "empty_candidate_blocked"
                else:
                    status = "planning_infeasible"
                return _new_case_result(
                    profile_id=profile_id,
                    dialogue_id=dialogue_id,
                    meal_window=meal_window,
                    status=status,
                    profile_constraints=profile_constraints,
                    integrated=integrated,
                    started_at=started_at,
                    detail=str(exc),
                    meal_period=meal_period,
                    candidate_counts=candidate_counts,
                    used_candidate_counts=used_candidate_counts,
                    sampling_seeds=sampling_seeds,
                )
            raise

        _assert_planned_result(result, planning_input)
        return _new_case_result(
            profile_id=profile_id,
            dialogue_id=dialogue_id,
            meal_window=meal_window,
            status="planned",
            profile_constraints=profile_constraints,
            integrated=integrated,
            started_at=started_at,
            detail="已证明唯一最优" + resolution_note,
            meal_period=meal_period,
            candidate_counts=candidate_counts,
            used_candidate_counts=used_candidate_counts,
            sampling_seeds=sampling_seeds,
            selected_recipes=[
                item["recipe_name"] for item in result["selected_dishes"]
            ],
            nutrition_score=result["nutrition_score"],
        )
    except Exception as exc:
        return _new_case_result(
            profile_id=profile_id,
            dialogue_id=dialogue_id,
            meal_window=meal_window,
            status="technical_failure",
            profile_constraints=profile_constraints,
            integrated=integrated,
            started_at=started_at,
            detail=f"{type(exc).__name__}：{exc}",
            meal_period=meal_period,
            candidate_counts=candidate_counts,
            used_candidate_counts=used_candidate_counts,
            sampling_seeds=sampling_seeds,
        )


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _status_label(status: str) -> str:
    labels = {
        "planned": "规划成功",
        "constraint_conflict": "约束冲突",
        "meal_period_blocked": "餐次待确认",
        "allergen_blocked": "过敏安全门禁",
        "empty_candidate_blocked": "存在空候选",
        "planning_infeasible": "硬约束无解",
        "technical_failure": "技术失败",
    }
    return labels.get(status, status)


def _generate_report(
    *,
    users: list[dict[str, Any]],
    dialogues: list[dict[str, Any]],
    dialogue_constraints: dict[int, dict[str, Any]],
    dialogue_timings: dict[int, float],
    cases: list[CaseResult],
    environment: dict[str, Any],
    total_elapsed: float,
    window_order: list[str],
    window_clocks_text: dict[str, str],
) -> None:
    """输出不依赖外部资源的端到端业务报告。"""

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    counts = Counter(case.status for case in cases)
    by_dialogue: dict[int, list[CaseResult]] = defaultdict(list)
    by_profile: dict[int, list[CaseResult]] = defaultdict(list)
    for case in cases:
        by_dialogue[case.dialogue_id].append(case)
        by_profile[case.profile_id].append(case)

    summary_blocks = []
    for window_name in window_order:
        window_counts = Counter(
            case.status
            for case in cases
            if case.meal_window == window_name
        )
        summary_blocks.append(
            f'<h3>{_escape(window_name)}（固定时钟 {window_clocks_text.get(window_name, "")}）</h3>'
            '<div class="cards">'
            + "".join(
                f'<div class="card"><span>{_escape(_status_label(status))}</span>'
                f'<strong>{count}</strong></div>'
                for status, count in window_counts.most_common()
            )
            + '</div>'
        )
    summary_cards = "".join(summary_blocks)
    dialogue_blocks = []
    for window_name in window_order:
        window_rows = []
        for dialogue in dialogues:
            dialogue_id = dialogue["id"]
            rows = [
                case for case in by_dialogue[dialogue_id]
                if case.meal_window == window_name
            ]
            status_counts = Counter(row.status for row in rows)
            extracted = dialogue_constraints.get(dialogue_id, {})
            window_rows.append(
                "<tr>"
                f"<td>{dialogue_id}</td>"
                f"<td>{_escape(dialogue['user_messages'][0])}</td>"
                f"<td>{_escape(extracted.get('meal_periods', '提取失败'))}</td>"
                f"<td>{_escape(extracted.get('diner_count', ''))}</td>"
                f"<td>{dialogue_timings.get(dialogue_id, 0):.3f}s</td>"
                f"<td>{status_counts.get('planned', 0)}</td>"
                f"<td>{_escape(', '.join(f'{_status_label(k)}={v}' for k, v in status_counts.items() if k != 'planned'))}</td>"
                "</tr>"
            )
        dialogue_blocks.append(
            f'<h3>{_escape(window_name)}（{_escape(window_clocks_text.get(window_name, ""))}）</h3>'
            '<div class="table-wrap"><table><thead><tr>'
            '<th>ID</th><th>原始对话</th><th>提取餐次</th><th>人数</th>'
            '<th>LLM耗时</th><th>规划成功</th><th>其他终态</th>'
            '</tr></thead><tbody>'
            + "".join(window_rows)
            + '</tbody></table></div>'
        )
    dialogue_rows = "".join(dialogue_blocks)

    user_by_id = {user["id"]: user for user in users}
    profile_rows = []
    for profile_id in sorted(by_profile):
        user = user_by_id[profile_id]
        rows = by_profile[profile_id]
        status_counts = Counter(row.status for row in rows)
        scores = [
            row.nutrition_score
            for row in rows
            if row.nutrition_score is not None
        ]
        average_score = (
            f"{sum(scores) / len(scores):.2f}" if scores else "-"
        )
        profile_rows.append(
            "<tr>"
            f"<td>{profile_id}</td>"
            f"<td>{_escape(user['性别'])} / {_escape(user['年龄'])}</td>"
            f"<td>{_escape(user.get('特殊人群', []))}</td>"
            f"<td>{_escape(user.get('过敏食材', []))}</td>"
            f"<td>{status_counts.get('planned', 0)}</td>"
            f"<td>{average_score}</td>"
            f"<td>{_escape(', '.join(f'{_status_label(k)}={v}' for k, v in status_counts.items() if k != 'planned'))}</td>"
            "</tr>"
        )

    detail_rows = []
    for case in cases:
        css_class = "ok" if case.status == "planned" else (
            "fail" if case.status == "technical_failure" else "blocked"
        )
        detail_rows.append(
            "<tr>"
            f"<td>{_escape(case.meal_window)}</td>"
            f"<td>{case.profile_id}</td>"
            f"<td>{case.dialogue_id}</td>"
            f'<td><span class="status {css_class}">{_escape(_status_label(case.status))}</span></td>'
            f"<td>{_escape(case.meal_period or '-')}</td>"
            f"<td>{_escape(case.diner_count if case.diner_count is not None else '-')}</td>"
            f"<td>{_escape(case.special_populations)}</td>"
            f"<td>{_escape(case.allergens)}</td>"
            f"<td>{case.dish_count}</td>"
            f"<td>{_escape(case.candidate_counts)}</td>"
            f"<td>{_escape(case.used_candidate_counts)}</td>"
            f"<td>{_escape(case.sampling_seeds)}</td>"
            f"<td>{_escape('、'.join(case.selected_recipes) or '-')}</td>"
            f"<td>{_escape(case.nutrition_score if case.nutrition_score is not None else '-')}</td>"
            f"<td>{case.elapsed_seconds:.4f}s</td>"
            f"<td>{_escape(case.detail)}</td>"
            "</tr>"
        )

    report_data = json.dumps(
        [asdict(case) for case in cases],
        ensure_ascii=False,
        default=str,
    ).replace("</", "<\\/")
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Spec_06 50×14 端到端业务报告</title>
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
  <h1>Spec_06 菜单规划：50份档案 × 14组单轮对话</h1>
  <p>真实 PostgreSQL + 真实 LLM + 真实 Neo4j + 预计算营养 + CP-SAT</p>
  <p>生成时间：{_escape(generated_at)}；总耗时：{total_elapsed:.3f} 秒</p>
</header>
<main>
  <section>
    <h2>执行口径</h2>
    <p class="note">14条对话只调用真实LLM各提取一次，再与50份档案交叉组合，共700条业务链路。每组菜品使用基础种子{CANDIDATE_RANDOM_SEED}随机抽取最多{CANDIDATE_LIMIT_PER_DISH}个候选，抽样后恢复原始顺序；没有约束放宽、配方缩放或无解fallback。每个组合在四个固定时钟时段各验证一次（早/午/晚与窗口外）；餐次未明确时按 Spec_07 餐次解析（业务时区 Asia/Shanghai）确定早/午/晚餐，无法确定才记为餐次待确认。</p>
    <ul>
      <li>数据：档案 {environment['profiles']}；PostgreSQL菜谱 {environment['postgres_recipes']}；营养 {environment['recipe_nutrition']}；Neo4j菜谱 {environment['neo4j_recipes']}。</li>
      <li>模型：{_escape(environment['llm_provider'])} / {_escape(environment['llm_model'])}，报告不记录地址或密钥。</li>
      <li>思考配置：enable_thinking={_escape(environment['enable_thinking'])}（LLM_ENABLE_THINKING 环境变量控制，默认关闭）。</li>
      <li>业务阻断属于有效终态；只有 <code>technical_failure</code> 属于测试失败。</li>
    </ul>
  </section>
  <section>
    <h2>结果总览（按时间段分组）</h2>
    <div class="cards"><div class="card"><span>总组合</span><strong>{len(cases)}</strong></div>{summary_cards}</div>
  </section>
  <section>
    <h2>按对话汇总</h2>
    <div class="table-wrap"><table><thead><tr><th>ID</th><th>原始对话</th><th>提取餐次</th><th>人数</th><th>LLM耗时</th><th>规划成功</th><th>其他终态</th></tr></thead><tbody>{''.join(dialogue_rows)}</tbody></table></div>
  </section>
  <section>
    <h2>按用户档案汇总</h2>
    <div class="table-wrap"><table><thead><tr><th>ID</th><th>性别/年龄</th><th>特殊人群</th><th>过敏</th><th>规划成功</th><th>成功平均分</th><th>其他终态</th></tr></thead><tbody>{''.join(profile_rows)}</tbody></table></div>
  </section>
  <section>
    <h2>2800条组合明细（4个时间段 × 700）</h2>
    <p class="muted">原候选数是Neo4j返回数量；入模候选数应用了每组随机最多{CANDIDATE_LIMIT_PER_DISH}个的测试口径。</p>
    <div class="table-wrap"><table><thead><tr><th>时间段</th><th>用户</th><th>对话</th><th>状态</th><th>餐次</th><th>人数</th><th>特殊人群</th><th>过敏</th><th>菜品组</th><th>原候选数</th><th>入模候选数</th><th>抽样种子</th><th>选中菜单</th><th>得分</th><th>耗时</th><th>说明</th></tr></thead><tbody>{''.join(detail_rows)}</tbody></table></div>
  </section>
  <section>
    <h2>验收结论</h2>
    <ul>
      <li>样本完整性：{len(users)}份档案 × {len(dialogues)}条单轮对话 = {len(cases)}条组合。</li>
      <li>技术失败：{counts.get('technical_failure', 0)}。</li>
      <li>规划成功：{counts.get('planned', 0)}；其余为契约规定的冲突、安全门禁、餐次待确认、空候选或硬约束无解。</li>
    </ul>
  </section>
</main>
<script type="application/json" id="case-data">{report_data}</script>
</body>
</html>"""
    REPORT_PATH.write_text(document, encoding="utf-8")


def test_LLM配置以env文件为准且不覆盖数据库配置(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_MODEL=qwen3.7-flash\nDATABASE_URL=env-database\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro[1m]")
    monkeypatch.setenv("DATABASE_URL", "process-database")

    _load_dotenv(env_path)

    assert os.environ["LLM_MODEL"] == "qwen3.7-flash"
    assert os.environ["DATABASE_URL"] == "process-database"


def test_候选随机抽取100道且保持原始顺序() -> None:
    candidates = [
        {"recipe_name": f"菜谱{index}"}
        for index in range(150)
    ]

    first, first_seed = _sample_candidate_group(
        candidates,
        profile_id=3,
        dialogue_id=4,
        dish_index=1,
    )
    second, second_seed = _sample_candidate_group(
        candidates,
        profile_id=3,
        dialogue_id=4,
        dish_index=1,
    )

    selected_indexes = [int(item["recipe_name"].removeprefix("菜谱")) for item in first]
    assert len(first) == 100
    assert first == second
    assert first_seed == second_seed == 42_030_401
    assert selected_indexes == sorted(selected_indexes)
    assert first != candidates[:100]


def test_候选不足100道时全部保留() -> None:
    candidates = [{"recipe_name": f"菜谱{index}"} for index in range(12)]

    sampled, _ = _sample_candidate_group(
        candidates,
        profile_id=1,
        dialogue_id=1,
        dish_index=0,
    )

    assert sampled == candidates


@pytest.mark.integration
def test_50份真实档案与14组单轮对话贯通到菜单规划() -> None:
    """运行700种组合，验证真实数据和外部服务一直贯通到CP-SAT。"""

    _load_dotenv()
    ensure_graph_data()
    assert os.environ.get("LLM_MODEL") == EXPECTED_LLM_MODEL, (
        "真实端到端测试必须使用.env中的qwen3.7-flash，实际为："
        f"{os.environ.get('LLM_MODEL')}"
    )
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
    )
    from backend.services.meal_period_resolution import (
        MealPeriodResolutionService,
    )

    # 餐次解析覆盖四个时间段：三个饭点窗口与窗口外，
    # 用固定时钟保证测试不依赖真实运行时刻（Spec_07 边界）
    window_clocks: dict[str, tuple[int, int]] = {
        "早餐时段": (7, 30),    # 05:00~10:00 内
        "午餐时段": (12, 0),    # 11:00~14:00 内
        "晚餐时段": (18, 30),   # 17:00~21:00 内
        "窗口外时段": (15, 30),  # 不在任何饭点窗口
    }
    window_services: list[tuple[str, MealPeriodResolutionService]] = [
        (
            window_name,
            MealPeriodResolutionService(
                clock=_fixed_clock(hour, minute),
                timezone_name="Asia/Shanghai",
            ),
        )
        for window_name, (hour, minute) in window_clocks.items()
    ]

    users = _load_json_array(USERS_PATH)
    dialogues = [
        item
        for item in _load_json_array(DIALOGUES_PATH)
        if item.get("turn_count") == 1
    ]
    assert len(users) == EXPECTED_PROFILE_COUNT
    assert len(dialogues) == EXPECTED_DIALOGUE_COUNT
    assert len({user["id"] for user in users}) == EXPECTED_PROFILE_COUNT
    assert len({item["id"] for item in dialogues}) == EXPECTED_DIALOGUE_COUNT

    started_at = time.perf_counter()
    cases: list[CaseResult] = []
    dialogue_timings: dict[int, float] = {}
    dialogue_constraints: dict[int, dict[str, Any]] = {}
    dialogue_errors: dict[int, str] = {}
    profile_constraints: dict[int, dict[str, Any]] = {}
    profile_errors: dict[int, str] = {}

    with create_constraint_services() as services:
        session_factory = services.profile._session_factory
        nutrition_service = NutritionService(session_factory)
        integration_service = ConstraintIntegrationService()
        menu_service = MenuPlanningService()

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
                dialogue_constraints[dialogue_id] = services.dialogue.extract(
                    dialogue
                )
            except Exception as exc:
                dialogue_errors[dialogue_id] = (
                    f"{type(exc).__name__}：{exc}"
                )
            dialogue_timings[dialogue_id] = round(
                time.perf_counter() - dialogue_started_at, 3
            )

        case_tasks: list[dict[str, Any]] = []
        for window_name, meal_period_service in window_services:
            for user in users:
                profile_id = user["id"]
                for dialogue in dialogues:
                    dialogue_id = dialogue["id"]
                    case_tasks.append(
                        {
                            "profile_id": profile_id,
                            "dialogue_id": dialogue_id,
                            "meal_window": window_name,
                            "profile_constraints": profile_constraints.get(
                                profile_id
                            ),
                            "dialogue_constraints": dialogue_constraints.get(
                                dialogue_id
                            ),
                            "profile_error": profile_errors.get(profile_id),
                            "dialogue_error": dialogue_errors.get(
                                dialogue_id
                            ),
                            "meal_period_service": meal_period_service,
                            "services": services,
                            "integration_service": integration_service,
                            "nutrition_service": nutrition_service,
                            "menu_service": menu_service,
                            "menu_error_type": MenuPlanningError,
                        }
                    )
        # 组合间相互独立，并行执行（图过滤与求解占主要耗时）
        with ThreadPoolExecutor(max_workers=8) as pool:
            cases = list(pool.map(lambda task: _run_case(**task), case_tasks))

    total_elapsed = time.perf_counter() - started_at
    environment = {
        **postgres_counts,
        "neo4j_recipes": neo4j_recipes,
        "llm_provider": os.environ.get("LLM_PROVIDER", "anthropic"),
        "llm_model": os.environ.get("LLM_MODEL", "未配置"),
        "enable_thinking": os.environ.get("LLM_ENABLE_THINKING", "false"),
    }
    # 先落盘结果数据：报告生成或后续断言出错时，数据仍可复用
    CASES_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    CASES_DATA_PATH.write_text(
        json.dumps(
            {
                "users": users,
                "dialogues": dialogues,
                "dialogue_constraints": dialogue_constraints,
                "dialogue_timings": dialogue_timings,
                "cases": [asdict(case) for case in cases],
                "environment": environment,
                "total_elapsed": total_elapsed,
                "window_order": [name for name, _ in window_services],
                "window_clocks_text": {
                    name: f"{hour:02d}:{minute:02d}"
                    for name, (hour, minute) in window_clocks.items()
                },
            },
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )
    _generate_report(
        users=users,
        dialogues=dialogues,
        dialogue_constraints=dialogue_constraints,
        dialogue_timings=dialogue_timings,
        cases=cases,
        environment=environment,
        total_elapsed=total_elapsed,
        window_order=[name for name, _ in window_services],
        window_clocks_text={
            name: f'{hour:02d}:{minute:02d}'
            for name, (hour, minute) in window_clocks.items()
        },
    )

    assert len(cases) == (
        EXPECTED_PROFILE_COUNT * EXPECTED_DIALOGUE_COUNT * len(window_services)
    )
    assert not profile_errors, f"档案约束提取失败：{profile_errors}"
    assert not dialogue_errors, f"对话约束提取失败：{dialogue_errors}"
    technical_failures = [
        case for case in cases if case.status == "technical_failure"
    ]
    assert not technical_failures, (
        "存在技术失败："
        + json.dumps(
            [asdict(case) for case in technical_failures],
            ensure_ascii=False,
            default=str,
        )
    )
    assert not [case for case in cases if case.status == "allergen_blocked"], (
        "蟹类等已知过敏概念不得进入未知过敏安全门禁"
    )
    assert all(
        "果蔬清洗" not in case.selected_recipes for case in cases
    ), "无效菜谱果蔬清洗不得进入菜单"
    assert any(case.status == "planned" for case in cases), (
        "700种组合没有一组成功生成菜单"
    )
