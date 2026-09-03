from __future__ import annotations

import html
import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = REPO_ROOT / "docs" / "交付" / "测试报告_50x20.html"
OUTPUT_PATH = REPO_ROOT / "docs" / "交付" / "测试报告_50x20_v1.html"

RAW_RESULTS_MARKER = '<section><h2>按对话逐轮输出（20组 × 50档案）</h2>'
DIALOGUE_START_PATTERN = re.compile(
    r"<section><h2>对话 (?P<dialogue_id>\d+)</h2>"
)


@dataclass(frozen=True)
class DialogueStats:
    dialogue_id: int
    query_html: str
    turn_count: int
    generated_count: int
    empty_count: int
    infeasible_count: int

    @property
    def total_count(self) -> int:
        return self.generated_count + self.empty_count + self.infeasible_count

    @property
    def abnormal_count(self) -> int:
        return self.empty_count + self.infeasible_count

    @property
    def abnormal_rate(self) -> float:
        return self.abnormal_count / self.total_count


@dataclass(frozen=True)
class AbnormalContext:
    dialogue_id: int
    turn_number: int
    user_message_html: str
    profile_id: int
    hard_html: str
    soft_html: str
    status: str


def extract_dialogue_stats(source: str) -> list[DialogueStats]:
    """从现有报告中提取每组对话的统计，不重新执行任何测试。"""

    marker_index = source.find(RAW_RESULTS_MARKER)
    if marker_index < 0:
        raise RuntimeError("现有报告中缺少原始结果区域")

    raw_results = source[marker_index:]
    matches = list(DIALOGUE_START_PATTERN.finditer(raw_results))
    if len(matches) != 20:
        raise RuntimeError(f"预期20组对话，实际找到{len(matches)}组")

    stats: list[DialogueStats] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw_results)
        block = raw_results[match.start() : end]
        query_match = re.search(
            r"<p class='note'>(?P<query>.*?)</p>",
            block,
            flags=re.DOTALL,
        )
        turn_match = re.search(r"<p>轮数：(?P<count>\d+)；", block)
        if query_match is None or turn_match is None:
            raise RuntimeError(
                f"对话{match.group('dialogue_id')}缺少问题或轮数信息"
            )

        stats.append(
            DialogueStats(
                dialogue_id=int(match.group("dialogue_id")),
                query_html=query_match.group("query"),
                turn_count=int(turn_match.group("count")),
                generated_count=block.count(
                    "<span class='status ok'>推荐成功</span>"
                ),
                empty_count=block.count(
                    "<span class='status blocked'>空候选</span>"
                ),
                infeasible_count=block.count(
                    "<span class='status blocked'>规划无解</span>"
                ),
            )
        )

    return stats


def extract_abnormal_contexts(source: str) -> list[AbnormalContext]:
    """按原报告顺序提取异常对应的对话、轮次和软硬约束。"""

    marker_index = source.find(RAW_RESULTS_MARKER)
    if marker_index < 0:
        raise RuntimeError("现有报告中缺少原始结果区域")

    raw_results = source[marker_index:]
    dialogue_matches = list(DIALOGUE_START_PATTERN.finditer(raw_results))
    turn_pattern = re.compile(
        r"<details(?: open| )><summary>第(?P<turn_number>\d+)轮："
        r"(?P<user_message>.*?)"
        r"（首Token .*?</summary><div class='table-wrap'><table>.*?<tbody>",
        flags=re.DOTALL,
    )
    abnormal_row_pattern = re.compile(
        r"<tr><td>(?P<profile_id>\d+)</td>"
        r"<td>(?P<hard>[^<]*)</td>"
        r"<td>(?P<soft>[^<]*)</td>"
        r"<td><span class='status blocked'>"
        r"(?P<status>空候选|规划无解)</span>",
    )

    contexts: list[AbnormalContext] = []
    for dialogue_index, dialogue_match in enumerate(dialogue_matches):
        dialogue_end = (
            dialogue_matches[dialogue_index + 1].start()
            if dialogue_index + 1 < len(dialogue_matches)
            else len(raw_results)
        )
        dialogue_block = raw_results[dialogue_match.start() : dialogue_end]
        turn_matches = list(turn_pattern.finditer(dialogue_block))
        for turn_index, turn_match in enumerate(turn_matches):
            turn_end = (
                turn_matches[turn_index + 1].start()
                if turn_index + 1 < len(turn_matches)
                else len(dialogue_block)
            )
            turn_block = dialogue_block[turn_match.end() : turn_end]
            for row_match in abnormal_row_pattern.finditer(turn_block):
                contexts.append(
                    AbnormalContext(
                        dialogue_id=int(dialogue_match.group("dialogue_id")),
                        turn_number=int(turn_match.group("turn_number")),
                        user_message_html=turn_match.group("user_message"),
                        profile_id=int(row_match.group("profile_id")),
                        hard_html=row_match.group("hard"),
                        soft_html=row_match.group("soft"),
                        status=row_match.group("status"),
                    )
                )

    if len(contexts) != 153:
        raise RuntimeError(f"预期153条异常上下文，实际找到{len(contexts)}条")
    return contexts


def calculate_percent(numerator: int, denominator: int, digits: int = 2) -> str:
    """按固定小数位输出百分比。"""

    return f"{numerator / denominator * 100:.{digits}f}%"


def calculate_percentile(values: list[float], percentile: float) -> float:
    """按 nearest-rank 口径计算百分位。"""

    sorted_values = sorted(values)
    index = max(0, math.ceil(percentile * len(sorted_values)) - 1)
    return sorted_values[index]


def deduplicate_soft_target(cell_html: str) -> str:
    """仅在展示层合并重复的口味、人群等软目标。"""

    if "<" in cell_html:
        return cell_html

    decoded = html.unescape(cell_html)
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    plain_parts: list[str] = []
    for raw_part in decoded.split("；"):
        part = raw_part.strip()
        if not part:
            continue
        if "：" not in part:
            if part not in plain_parts:
                plain_parts.append(part)
            continue
        label, raw_values = part.split("：", 1)
        if label not in groups:
            groups[label] = []
            order.append(label)
        for value in raw_values.split("、"):
            normalized = value.strip()
            if normalized and normalized not in groups[label]:
                groups[label].append(normalized)

    merged = [f"{label}：{'、'.join(groups[label])}" for label in order]
    merged.extend(plain_parts)
    return html.escape("；".join(merged), quote=False)


def build_dialogue_summary_rows(stats: list[DialogueStats]) -> str:
    """生成20组对话摘要表。"""

    rows: list[str] = []
    for item in stats:
        rows.append(
            "<tr>"
            f"<td><a href='#dialogue-{item.dialogue_id}'>对话{item.dialogue_id}</a></td>"
            f"<td class='query-cell'>{item.query_html}</td>"
            f"<td>{item.turn_count}</td>"
            f"<td>{item.generated_count}</td>"
            f"<td>{item.empty_count}</td>"
            f"<td>{item.infeasible_count}</td>"
            f"<td>{calculate_percent(item.abnormal_count, item.total_count, 1)}</td>"
            "</tr>"
        )
    return "".join(rows)


def build_navigation(stats: list[DialogueStats]) -> str:
    """生成报告目录和对话锚点。"""

    dialogue_links = "".join(
        f"<a href='#dialogue-{item.dialogue_id}'>{item.dialogue_id}</a>"
        for item in stats
    )
    return (
        "<nav class='toc' aria-label='报告目录'>"
        "<strong>报告目录</strong>"
        "<div class='toc-main'>"
        "<a href='#summary'>结论摘要</a>"
        "<a href='#methodology'>测试方法</a>"
        "<a href='#dialogue-summary'>对话汇总</a>"
        "<a href='#risk-focus'>重点风险</a>"
        "<a href='#performance'>内部耗时</a>"
        "<a href='#exception-details'>异常明细</a>"
        "<a href='#raw-results'>原始结果</a>"
        "</div>"
        f"<div class='toc-dialogues'><span>对话：</span>{dialogue_links}</div>"
        "</nav>"
    )


def build_overview_sections(
    stats: list[DialogueStats],
    nutrition_scores: list[int],
    first_token_values: list[float],
) -> str:
    """生成预览版首页的摘要、方法、风险和性能区域。"""

    total_count = sum(item.total_count for item in stats)
    generated_count = sum(item.generated_count for item in stats)
    empty_count = sum(item.empty_count for item in stats)
    infeasible_count = sum(item.infeasible_count for item in stats)
    low_nutrition_count = sum(score < 8 for score in nutrition_scores)
    risk_stats = [item for item in stats if item.dialogue_id in {7, 8, 20}]
    risk_abnormal_count = sum(item.abnormal_count for item in risk_stats)
    abnormal_count = empty_count + infeasible_count
    summary_rows = build_dialogue_summary_rows(stats)

    cards = "".join(
        (
            f"<div class='card {css_class}'><span>{label}</span>"
            f"<strong>{value}</strong><small>{note}</small></div>"
        )
        for label, value, note, css_class in (
            ("档案×轮次组合", str(total_count), "20组对话，共29轮", ""),
            (
                "成功生成",
                str(generated_count),
                f"生成率 {calculate_percent(generated_count, total_count)}",
                "generated-card",
            ),
            ("空候选", str(empty_count), "未找到候选菜品", "empty-card"),
            ("规划无解", str(infeasible_count), "候选存在但组合不可行", "fail-card"),
            (
                "低营养得分",
                str(low_nutrition_count),
                "已生成推荐中得分低于8分",
                "warning-card",
            ),
            ("严格验收通过率", "待复核", "尚未执行硬约束与真实性校验", "review-card"),
        )
    )

    risk_rows = "".join(
        "<tr>"
        f"<td><a href='#dialogue-{item.dialogue_id}'>对话{item.dialogue_id}</a></td>"
        f"<td>{item.query_html}</td>"
        f"<td>{item.abnormal_count}</td>"
        f"<td>{calculate_percent(item.abnormal_count, item.total_count, 1)}</td>"
        "</tr>"
        for item in risk_stats
    )

    average = statistics.mean(first_token_values)
    median = statistics.median(first_token_values)
    p95 = calculate_percentile(first_token_values, 0.95)
    maximum = max(first_token_values)
    under_five_count = sum(value < 5 for value in first_token_values)

    return f"""
<section id="summary"><h2>测试结论摘要</h2>
<div class="cards summary-cards">{cards}</div>
<div class="conclusion-grid">
<p class="note"><strong>当前结论：</strong>本报告证明系统能够在1450个档案×轮次组合中生成1297个推荐，但“成功生成”不等于“严格验收通过”。硬约束违规率和菜谱真实性违规率尚待专门校验。</p>
<p class="warn-note"><strong>使用边界：</strong>本版不把推荐生成数表述为通过数，也不把服务内部处理耗时表述为公网API性能。</p>
</div></section>
<section id="methodology"><h2>测试方法与口径</h2>
<ol class="method-list">
<li>20组对话样例共29轮，每轮先以档案25所在会话执行一次LLM约束提取。</li>
<li>同一轮提取出的对话约束分别与50份健康档案约束合并，再执行候选检索、菜单规划和模板回答组装。</li>
<li>因此1450表示“档案×对话轮次的功能回归组合”，不是1450次独立公网HTTP请求。</li>
<li>报告保留全部生成回答；当前只统计生成、空候选、规划无解和内部营养分，不计算硬约束及菜谱真实性的严格通过率。</li>
</ol>
<div class="review-grid">
<div><span>硬约束违规率</span><strong>待复核</strong><p>需校验过敏、忌口、餐次、时间、难度和必需食材。</p></div>
<div><span>菜谱真实性违规率</span><strong>待复核</strong><p>需逐项核对推荐菜名是否存在于正式菜谱库。</p></div>
</div></section>
<section id="dialogue-summary"><h2>20组对话汇总</h2>
<p class="section-intro">异常率＝（空候选＋规划无解）÷该对话的档案×轮次组合数。</p>
<div class="table-wrap summary-table"><table><thead><tr><th>对话</th><th>用户问题</th><th>轮数</th><th>成功生成</th><th>空候选</th><th>规划无解</th><th>异常率</th></tr></thead><tbody>{summary_rows}</tbody></table></div>
</section>
<section id="risk-focus"><h2>重点风险场景</h2>
<p class="warn-note">对话7、8、20共出现<strong>{risk_abnormal_count}</strong>个异常，占全部{abnormal_count}个异常的<strong>{calculate_percent(risk_abnormal_count, abnormal_count, 1)}</strong>，应优先分析食材限制、搭配要求和多人差异化口味的处理。</p>
<div class="table-wrap"><table><thead><tr><th>对话</th><th>场景</th><th>异常数</th><th>异常率</th></tr></thead><tbody>{risk_rows}</tbody></table></div>
</section>
<section id="performance"><h2>服务内部处理耗时</h2>
<p class="warn-note"><strong>非公网API实测：</strong>以下数据来自档案25的服务内部调用，包含LLM约束提取和菜单生成，不包含公网网络传输、HTTP框架、SSE分块到达及并发等待时间。</p>
<div class="metric-strip">
<div><span>样本数</span><strong>{len(first_token_values)}</strong></div>
<div><span>平均值</span><strong>{average:.2f}s</strong></div>
<div><span>p50</span><strong>{median:.2f}s</strong></div>
<div><span>p95</span><strong>{p95:.2f}s</strong></div>
<div><span>最大值</span><strong>{maximum:.2f}s</strong></div>
<div><span>&lt;5秒</span><strong>{under_five_count}/{len(first_token_values)}</strong></div>
</div>
<p class="section-intro">当前回答由模板一次组装完成，所以报告中的“首内容可用时间”和“单轮内部完成时间”几乎相同；正式版需在公网部署后重新测量真实TTFT和流式完成时间。</p>
</section>
"""


def build_preview(source: str) -> str:
    """在不改变原始测试结果的前提下重排报告。"""

    stats = extract_dialogue_stats(source)
    abnormal_contexts = extract_abnormal_contexts(source)
    marker_index = source.index(RAW_RESULTS_MARKER)
    raw_results = source[marker_index:]
    nutrition_scores = [
        int(match.group("score"))
        for match in re.finditer(
            r"<div class='muted'>营养分 (?P<score>\d+)/16</div>",
            raw_results,
        )
    ]
    first_token_values = [
        float(match.group("seconds"))
        for match in re.finditer(
            r"首Token (?P<seconds>\d+(?:\.\d+)?)s｜单轮端到端",
            raw_results,
        )
    ]
    if len(nutrition_scores) != 1297 or len(first_token_values) != 29:
        raise RuntimeError(
            "现有报告的营养分或性能样本数量与预期不一致："
            f"nutrition={len(nutrition_scores)}, performance={len(first_token_values)}"
        )

    metadata_match = re.search(
        r"<header>.*?<p>生成时间：(?P<metadata>.*?)</p></header>",
        source,
        flags=re.DOTALL,
    )
    if metadata_match is None:
        raise RuntimeError("现有报告中缺少生成环境信息")

    preview = re.sub(
        r"<title>.*?</title>",
        "<title>个性化膳食规划Agent · 50×20 功能回归测试报告（第一版预览）</title>",
        source,
        count=1,
        flags=re.DOTALL,
    )
    preview = re.sub(
        r"<header>.*?</header>",
        (
            "<header>"
            "<span class='preview-badge'>第一版预览</span>"
            "<h1>个性化膳食规划Agent · 50×20 功能回归测试报告</h1>"
            "<p>20组对话用例 × 50份用户档案（共29轮）· 服务层功能回归</p>"
            f"<p>生成时间：{metadata_match.group('metadata')}</p>"
            "</header>"
        ),
        preview,
        count=1,
        flags=re.DOTALL,
    )

    overview = build_navigation(stats) + build_overview_sections(
        stats,
        nutrition_scores,
        first_token_values,
    )
    preview, overview_count = re.subn(
        r"<section><h2>总览</h2>.*?</section>",
        overview,
        preview,
        count=1,
        flags=re.DOTALL,
    )
    if overview_count != 1:
        raise RuntimeError("无法替换现有报告总览区域")

    preview = preview.replace(
        "<section><h2>异常处理</h2>",
        '<section id="exception-details"><h2>异常明细</h2>',
        1,
    )
    preview = preview.replace(
        RAW_RESULTS_MARKER,
        (
            '<section id="raw-results" class="raw-results">'
            "<h2>原始结果附录（20组 × 50档案）</h2>"
            "<p class='note'>以下保留原报告的1450条生成回答。为降低页面信息密度，所有轮次和回答默认折叠；本区域在打印摘要PDF时隐藏。</p>"
        ),
        1,
    )

    preview = DIALOGUE_START_PATTERN.sub(
        lambda match: (
            f"<section class='dialogue-section' id='dialogue-{match.group('dialogue_id')}'>"
            f"<h3>对话 {match.group('dialogue_id')}</h3>"
        ),
        preview,
    )
    preview = preview.replace(
        "<details open><summary>第",
        "<details class='turn-detail'><summary>第",
    )
    preview = preview.replace(
        "<details ><summary>第",
        "<details class='turn-detail'><summary>第",
    )
    preview = preview.replace(
        "<details><summary>查看回答</summary>",
        "<details class='answer-detail'><summary>查看回答</summary>",
    )
    preview = preview.replace("当前轮API输出", "当前轮生成回答")

    raw_preview_index = preview.index('<section id="raw-results"')
    prefix = preview[:raw_preview_index]
    raw_preview = preview[raw_preview_index:]
    row_pattern = re.compile(
        r"(?P<prefix><tr><td>\d+</td><td>.*?</td><td>)"
        r"(?P<soft>.*?)"
        r"(?P<suffix></td><td>)",
        flags=re.DOTALL,
    )
    raw_preview = row_pattern.sub(
        lambda match: (
            match.group("prefix")
            + deduplicate_soft_target(match.group("soft"))
            + match.group("suffix")
        ),
        raw_preview,
    )
    preview = prefix + raw_preview

    def replace_generated_status(match: re.Match[str]) -> str:
        score = int(match.group("score"))
        css_class = "quality-warning" if score < 8 else "generated"
        label = "生成推荐·质量警告" if score < 8 else "生成推荐"
        return (
            f"<span class='status {css_class}'>{label}</span>"
            f"<div class='muted'>营养分 {score}/16</div>"
        )

    preview = re.sub(
        r"<span class='status ok'>推荐成功</span>"
        r"<div class='muted'>营养分 (?P<score>\d+)/16</div>",
        replace_generated_status,
        preview,
    )
    preview = preview.replace(
        "<span class='status blocked'>空候选</span>",
        "<span class='status empty'>空候选</span>",
    )
    preview = preview.replace(
        "<span class='status blocked'>规划无解</span>",
        "<span class='status infeasible'>规划无解</span>",
    )

    anomaly_pattern = re.compile(
        r"(?P<start><section id=\"exception-details\">.*?<tbody>)"
        r"(?P<rows>.*?)"
        r"(?P<end></tbody></table></div>\s*<h3>LLM 提取异常记录)",
        flags=re.DOTALL,
    )
    anomaly_match = anomaly_pattern.search(preview)
    if anomaly_match is None:
        raise RuntimeError("无法定位异常明细表")
    anomaly_row_pattern = re.compile(
        r"<tr><td>(?P<profile_id>.*?)</td>"
        r"<td>(?P<hard>.*?)</td>"
        r"<td>(?P<status>.*?)</td>"
        r"<td>(?P<detail>.*?)</td></tr>",
        flags=re.DOTALL,
    )
    anomaly_rows = list(anomaly_row_pattern.finditer(anomaly_match.group("rows")))
    if len(anomaly_rows) != len(abnormal_contexts):
        raise RuntimeError(
            "异常明细与原始结果数量不一致："
            f"detail={len(anomaly_rows)}, context={len(abnormal_contexts)}"
        )

    enriched_rows: list[str] = []
    for row_match, context in zip(anomaly_rows, abnormal_contexts, strict=True):
        profile_text = html.unescape(
            re.sub(r"<[^>]+>", "", row_match.group("profile_id"))
        ).strip()
        hard_text = html.unescape(
            re.sub(r"<[^>]+>", "", row_match.group("hard"))
        ).strip()
        status_text = html.unescape(
            re.sub(r"<[^>]+>", "", row_match.group("status"))
        ).strip()
        expected_hard = html.unescape(
            re.sub(r"<[^>]+>", "", context.hard_html)
        ).strip()
        if (
            int(profile_text) != context.profile_id
            or hard_text != expected_hard
            or status_text != context.status
        ):
            raise RuntimeError(
                "异常顺序不一致："
                f"context=({context.dialogue_id}, {context.turn_number}, "
                f"{context.profile_id}), "
                f"profile_equal={int(profile_text) == context.profile_id}, "
                f"hard_equal={hard_text == expected_hard}, "
                f"status_equal={status_text == context.status}"
            )

        enriched_rows.append(
            "<tr>"
            f"<td><a class='anomaly-link' href='#dialogue-{context.dialogue_id}'>"
            f"对话{context.dialogue_id}</a>"
            f"<div class='muted'>第{context.turn_number}轮</div></td>"
            f"<td class='anomaly-query'>{context.user_message_html}</td>"
            f"<td>{context.profile_id}</td>"
            f"<td>{context.hard_html}</td>"
            f"<td>{context.soft_html}</td>"
            f"<td>{row_match.group('status')}</td>"
            "<td><details class='tech-detail'><summary>查看技术详情</summary><code>"
            f"{row_match.group('detail')}"
            "</code></details></td>"
            "</tr>"
        )

    anomaly_start = anomaly_match.group("start").replace(
        "<tr><th>档案</th><th>硬约束</th><th>状态</th><th>详情</th></tr>",
        "<tr><th>对话/轮次</th><th>本轮用户问题</th><th>档案</th>"
        "<th>硬约束</th><th>软目标</th><th>状态</th><th>详情</th></tr>",
        1,
    )
    anomaly_start = anomaly_start.replace(
        '<div class="table-wrap"><table>',
        '<div class="table-wrap anomaly-table"><table>',
        1,
    )
    preview = (
        preview[: anomaly_match.start()]
        + anomaly_start
        + "".join(enriched_rows)
        + anomaly_match.group("end")
        + preview[anomaly_match.end() :]
    )

    extra_css = r"""
.preview-badge { display:inline-block;margin-bottom:12px;padding:5px 12px;border:1px solid #bfdbfe;border-radius:999px;background:#dbeafe;color:#0c3175;font-weight:700;letter-spacing:.04em; }
.toc { max-width:1680px;margin:24px auto;padding:18px 22px;border:1px solid var(--line);border-radius:12px;background:white;box-shadow:0 4px 16px #23395d0c; }
.toc strong { display:block;margin-bottom:10px;font-size:16px; }
.toc-main,.toc-dialogues { display:flex;flex-wrap:wrap;gap:8px;align-items:center; }
.toc-dialogues { margin-top:10px; }
.toc a { padding:4px 9px;border-radius:7px;color:#124fb4;background:#eef4ff;text-decoration:none; }
.toc a:hover { background:#dbeafe; }
h3 { margin:0 0 12px;font-size:18px; }
.card small { display:block;margin-top:6px;color:var(--muted);line-height:1.4; }
.generated-card { border-color:#bfdbfe;background:#eff6ff; }
.empty-card,.warning-card { border-color:#fde68a;background:#fffbeb; }
.fail-card { border-color:#fecaca;background:#fff1f2; }
.review-card { border-style:dashed;border-color:#94a3b8;background:#f8fafc; }
.conclusion-grid { display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:16px; }
.conclusion-grid p { margin:0; }
.method-list { margin:0;padding-left:22px; }
.method-list li + li { margin-top:8px; }
.review-grid { display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:16px; }
.review-grid > div { padding:14px;border:1px dashed #94a3b8;border-radius:10px;background:#f8fafc; }
.review-grid span { display:block;color:var(--muted); }
.review-grid strong { display:block;margin:4px 0;font-size:20px; }
.review-grid p { margin:0;color:var(--muted); }
.section-intro { color:var(--muted); }
.summary-table { max-height:none; }
.summary-table .query-cell { min-width:340px;max-width:680px; }
.metric-strip { display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px; }
.metric-strip > div { padding:12px;border:1px solid var(--line);border-radius:9px;background:#fbfcfe; }
.metric-strip span { display:block;color:var(--muted); }
.metric-strip strong { display:block;margin-top:3px;font-size:21px; }
.status.generated { color:#124fb4;background:#eaf2ff; }
.status.quality-warning { color:#8a5b00;background:#fff4d6; }
.status.empty { color:#a23b00;background:#fff0e5; }
.status.infeasible { color:#b42318;background:#feeceb; }
.dialogue-section { box-shadow:none;border-color:#e5eaf0;background:#fcfdff; }
.turn-detail > summary { padding:9px 11px;border-radius:8px;background:#f3f6fa; }
.turn-detail[open] > summary { margin-bottom:10px; }
.answer-detail > summary,.tech-detail > summary { color:#124fb4; }
.tech-detail code { display:block;margin-top:8px;padding:10px;border-radius:7px;background:#f7f9fb;white-space:pre-wrap;word-break:break-word; }
.anomaly-table { max-height:720px; }
.anomaly-table .anomaly-query { min-width:240px;max-width:420px; }
.anomaly-link { font-weight:700;color:#124fb4;text-decoration:none; }
@media (max-width:860px) {
  main { padding:12px; }
  header { padding:26px 18px; }
  header h1 { font-size:24px; }
  section,.toc { padding:16px; }
  .conclusion-grid,.review-grid { grid-template-columns:1fr; }
  .summary-table .query-cell { min-width:260px; }
}
@media print {
  @page { size:A4 landscape;margin:12mm; }
  body { background:white;color:#111;font-size:10pt; }
  header { padding:0 0 14px;color:#111;background:white;border-bottom:2px solid #155eef; }
  header h1 { font-size:21pt; }
  .preview-badge { color:#111;background:white;border-color:#777; }
  main,.toc { max-width:none;margin:0;padding:0;box-shadow:none; }
  .toc { margin:12px 0;border:none; }
  .toc-dialogues { display:none; }
  section { break-inside:avoid;margin:0 0 12px;padding:12px;box-shadow:none; }
  .table-wrap { max-height:none;overflow:visible; }
  table { font-size:8.5pt; }
  th { position:static; }
  a { color:#111;text-decoration:none; }
  #exception-details,#raw-results { display:none; }
}
"""
    preview = preview.replace("</style>", extra_css + "</style>", 1)
    return preview


def validate_preview(source: str, preview: str) -> None:
    """校验预览版没有丢失原始回答或对话。"""

    source_answers = re.findall(
        r"<pre class='answer'>(.*?)</pre>",
        source,
        flags=re.DOTALL,
    )
    preview_answers = re.findall(
        r"<pre class='answer'>(.*?)</pre>",
        preview,
        flags=re.DOTALL,
    )
    if source_answers != preview_answers:
        raise RuntimeError("预览版中的原始回答与现有报告不一致")
    if len(preview_answers) != 1450:
        raise RuntimeError(f"预览版回答数量异常：{len(preview_answers)}")
    if len(re.findall(r"id='dialogue-\d+'", preview)) != 20:
        raise RuntimeError("预览版对话锚点数量不是20")
    if len(re.findall(r"class='turn-detail'", preview)) != 29:
        raise RuntimeError("预览版轮次折叠区域数量不是29")
    if len(re.findall(r"class='anomaly-link'", preview)) != 153:
        raise RuntimeError("预览版异常定位链接数量不是153")
    if (
        "<details open>" in preview
        or "<details ><summary>第" in preview
        or "<h2>对话 " in preview
    ):
        raise RuntimeError("预览版仍存在默认展开轮次或错误标题层级")
    for required_text in (
        "第一版预览",
        "成功生成",
        "89.45%",
        "低营养得分",
        "154",
        "p95",
        "5.03s",
        "118",
        "77.1%",
        "严格验收通过率",
        "待复核",
        "原始结果附录",
        "本轮用户问题",
        "软目标",
    ):
        if required_text not in preview:
            raise RuntimeError(f"预览版缺少关键内容：{required_text}")


def main() -> None:
    """生成独立测试报告预览文件。"""

    source = SOURCE_PATH.read_text(encoding="utf-8")
    preview = build_preview(source)
    validate_preview(source, preview)
    OUTPUT_PATH.write_text(preview, encoding="utf-8")
    print(f"测试报告预览已生成：{OUTPUT_PATH}")


if __name__ == "__main__":
    main()
