from __future__ import annotations

import html
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping


AUDIT_STATUSES = frozenset({"pass", "fail", "not_applicable", "not_auditable"})
DIFFICULTY_RANK = {"简单": 1, "中等": 2, "复杂": 3}
TASTE_LABELS = {
    "is_sweet": "甜",
    "is_light": "清淡",
    "is_spicy": "辣",
    "is_salty": "咸",
    "is_sour": "酸",
}
PROFILE_TASTE_TOKENS = (
    ("不甜", "甜", False),
    ("不咸", "咸", False),
    ("清淡", "清淡", True),
    ("甜", "甜", True),
    ("辣", "辣", True),
    ("咸", "咸", True),
    ("酸", "酸", True),
)


class AcceptanceAuditError(RuntimeError):
    """独立验收输入、数据源或规则执行失败。"""


@dataclass(frozen=True)
class RecipeAuditRecord:
    """一个数据源中的菜谱验收投影。"""

    name: str
    is_recommendable: bool
    tags: frozenset[str]
    difficulty: str
    total_time_minutes: int
    dish_type: str | None
    ingredients: frozenset[str]
    core_ingredients: frozenset[str]
    ingredient_categories: Mapping[str, str | None]
    nutrition: Mapping[str, Decimal]


@dataclass(frozen=True)
class ReportCase:
    """从现有逐轮HTML报告恢复出的单个档案×轮次结果。"""

    dialogue_id: int
    turn_number: int
    user_message: str
    profile_id: int
    hard_text: str
    soft_text: str
    generation_status: str
    answer_text: str
    selected_recipes: tuple[str, ...]
    answer_diner_count: int | None


def parse_delivery_report(path: Path) -> list[ReportCase]:
    """解析第一版交付报告中的1450条逐轮展示结果。"""

    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AcceptanceAuditError(f"无法读取逐轮报告：{path}") from exc

    starts = list(
        re.finditer(
            r"<section class='dialogue-section' id='dialogue-(\d+)'>",
            source,
        )
    )
    if len(starts) != 20:
        raise AcceptanceAuditError(
            f"逐轮报告应包含20组对话，实际为{len(starts)}组"
        )

    cases: list[ReportCase] = []
    for index, start in enumerate(starts):
        dialogue_id = int(start.group(1))
        end = starts[index + 1].start() if index + 1 < len(starts) else len(source)
        block = source[start.start() : end]
        turn_starts = list(
            re.finditer(
                r"<details class='turn-detail'><summary>第(\d+)轮：(.*?)"
                r"（首Token .*?</summary>",
                block,
                flags=re.DOTALL,
            )
        )
        for turn_index, turn_start in enumerate(turn_starts):
            turn_number = int(turn_start.group(1))
            user_message = _plain_text(turn_start.group(2))
            turn_end = (
                turn_starts[turn_index + 1].start()
                if turn_index + 1 < len(turn_starts)
                else len(block)
            )
            turn_block = block[turn_start.end() : turn_end]
            cases.extend(
                _parse_turn_rows(
                    dialogue_id,
                    turn_number,
                    user_message,
                    turn_block,
                )
            )

    if len(cases) != 1450:
        raise AcceptanceAuditError(
            f"逐轮报告应包含1450条档案×轮次结果，实际为{len(cases)}条"
        )
    return cases


def compare_catalogs(
    reference: Mapping[str, RecipeAuditRecord],
    postgres: Mapping[str, RecipeAuditRecord],
    graph: Mapping[str, RecipeAuditRecord],
    *,
    graph_tag_names: frozenset[str],
) -> list[dict[str, Any]]:
    """按菜名比较正式文件、PostgreSQL和Neo4j的共同字段。"""

    issues: list[dict[str, Any]] = []
    all_names = sorted(set(reference) | set(postgres) | set(graph))
    for name in all_names:
        ref = reference.get(name)
        pg = postgres.get(name)
        neo = graph.get(name)
        for source_name, value in (("正式JSON", ref), ("PostgreSQL", pg), ("Neo4j", neo)):
            if value is None:
                issues.append(
                    _consistency_issue(name, "recipe_exists", source_name, True, False)
                )
        if ref is None or pg is None or neo is None:
            continue

        _compare_field(issues, name, "is_recommendable", ref.is_recommendable, pg.is_recommendable, "PostgreSQL")
        _compare_field(issues, name, "is_recommendable", ref.is_recommendable, neo.is_recommendable, "Neo4j")
        _compare_field(issues, name, "labels", sorted(ref.tags), sorted(pg.tags), "PostgreSQL")
        _compare_field(
            issues,
            name,
            "graph_tags",
            sorted(ref.tags & graph_tag_names),
            sorted(neo.tags),
            "Neo4j",
        )
        _compare_field(issues, name, "difficulty", ref.difficulty, pg.difficulty, "PostgreSQL")
        _compare_field(issues, name, "difficulty", ref.difficulty, neo.difficulty, "Neo4j")
        _compare_field(issues, name, "total_time_minutes", ref.total_time_minutes, pg.total_time_minutes, "PostgreSQL")
        _compare_field(issues, name, "total_time_minutes", ref.total_time_minutes, neo.total_time_minutes, "Neo4j")
        _compare_field(issues, name, "dish_type", ref.dish_type, pg.dish_type, "PostgreSQL")
        _compare_field(issues, name, "dish_type", ref.dish_type, neo.dish_type, "Neo4j")
        _compare_field(issues, name, "ingredients", sorted(ref.ingredients), sorted(pg.ingredients), "PostgreSQL")
        _compare_field(issues, name, "ingredients", sorted(ref.ingredients), sorted(neo.ingredients), "Neo4j")
        _compare_field(
            issues,
            name,
            "ingredient_categories",
            dict(sorted(ref.ingredient_categories.items())),
            dict(sorted(pg.ingredient_categories.items())),
            "PostgreSQL",
        )
        _compare_field(
            issues,
            name,
            "ingredient_categories",
            dict(sorted(ref.ingredient_categories.items())),
            dict(sorted(neo.ingredient_categories.items())),
            "Neo4j",
        )
        _compare_field(issues, name, "core_ingredients", sorted(ref.core_ingredients), sorted(pg.core_ingredients), "PostgreSQL")
        _compare_field(issues, name, "core_ingredients", sorted(ref.core_ingredients), sorted(neo.core_ingredients), "Neo4j")
    return issues


def audit_report_case(
    case: ReportCase,
    expected: Mapping[str, Any],
    profile: Mapping[str, Any],
    reference: Mapping[str, RecipeAuditRecord],
    postgres: Mapping[str, RecipeAuditRecord],
    graph: Mapping[str, RecipeAuditRecord],
    concept_members: Mapping[str, frozenset[str]],
    inconsistent_recipes: frozenset[str],
) -> dict[str, Any]:
    """独立复核一个档案×轮次结果。"""

    result: dict[str, Any] = {
        "dialogue_id": case.dialogue_id,
        "turn_number": case.turn_number,
        "profile_id": case.profile_id,
        "user_message": case.user_message,
        "generation_status": case.generation_status,
        "selected_recipes": list(case.selected_recipes),
        "hard_constraint_status": "not_applicable",
        "authenticity_status": "not_applicable",
        "preference_status": "not_applicable",
        "requirements_coverage_status": "not_applicable",
        "strict_status": "not_recommended",
        "rules": [],
    }
    if case.generation_status != "recommended":
        return result

    rules: list[dict[str, Any]] = []
    recipes = [reference.get(name) for name in case.selected_recipes]
    for name in case.selected_recipes:
        _append_rule(rules, "auth.json_exists", "authenticity", "pass" if name in reference else "fail", name, name if name in reference else None, name)
        _append_rule(rules, "auth.postgres_exists", "authenticity", "pass" if name in postgres else "fail", name, name if name in postgres else None, name)
        _append_rule(rules, "auth.neo4j_exists", "authenticity", "pass" if name in graph else "fail", name, name if name in graph else None, name)
        source_records = [item for item in (reference.get(name), postgres.get(name), graph.get(name)) if item is not None]
        recommendable_status = "not_auditable" if len(source_records) != 3 else ("pass" if all(item.is_recommendable for item in source_records) else "fail")
        _append_rule(rules, "auth.recommendable", "authenticity", recommendable_status, True, [item.is_recommendable for item in source_records], name)
        _append_rule(rules, "auth.data_consistent", "authenticity", "fail" if name in inconsistent_recipes else "pass", "三方一致", "存在差异" if name in inconsistent_recipes else "三方一致", name)

    _append_rule(
        rules,
        "auth.answer_has_menu",
        "authenticity",
        "pass" if case.selected_recipes else "fail",
        "至少一道编号菜品",
        list(case.selected_recipes),
        "回答文本编号菜单行",
    )

    if any(recipe is None for recipe in recipes):
        _append_rule(rules, "hard.recipe_data_available", "hard_constraint", "not_auditable", "全部菜谱可读取", "存在缺失菜谱", "正式JSON")
    else:
        typed_recipes = [recipe for recipe in recipes if recipe is not None]
        _audit_global_constraints(rules, case, expected, profile, typed_recipes, concept_members, inconsistent_recipes)
        _audit_dish_groups(rules, expected, typed_recipes, concept_members)
        _audit_preferences(rules, expected, profile, typed_recipes, concept_members)

    for requirement in expected.get("unsupported", []):
        _append_rule(
            rules,
            "coverage.unsupported_requirement",
            "requirements_coverage",
            "not_auditable",
            requirement,
            "当前结构化模型未覆盖",
            case.user_message,
        )

    hard_status = _aggregate_status(rules, "hard_constraint")
    auth_status = _aggregate_status(rules, "authenticity")
    preference_status = _aggregate_status(rules, "preference")
    coverage_status = _aggregate_status(rules, "requirements_coverage")
    result["hard_constraint_status"] = hard_status
    result["authenticity_status"] = auth_status
    result["preference_status"] = preference_status
    result["requirements_coverage_status"] = coverage_status
    result["strict_status"] = (
        "passed"
        if hard_status == "pass" and auth_status == "pass"
        else "failed"
        if "fail" in {hard_status, auth_status}
        else "not_auditable"
    )
    result["rules"] = rules
    return result


def summarize_audits(audits: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """汇总生成率、严格通过率和逐规则统计。"""

    rows = list(audits)
    generation_counts = Counter(row["generation_status"] for row in rows)
    strict_counts = Counter(row["strict_status"] for row in rows)
    recommended = generation_counts.get("recommended", 0)
    hard_pass = sum(row["hard_constraint_status"] == "pass" for row in rows)
    auth_pass = sum(row["authenticity_status"] == "pass" for row in rows)
    preference_counts = Counter(row["preference_status"] for row in rows)
    coverage_counts = Counter(row["requirements_coverage_status"] for row in rows)
    rule_summary: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        for rule in row["rules"]:
            rule_summary[rule["rule_id"]][rule["status"]] += 1
    return {
        "total": len(rows),
        "generation_counts": dict(generation_counts),
        "strict_counts": dict(strict_counts),
        "generation_rate": _rate(recommended, len(rows)),
        "recommended_hard_pass_rate": _rate(hard_pass, recommended),
        "recommended_authenticity_pass_rate": _rate(auth_pass, recommended),
        "overall_strict_pass_rate": _rate(strict_counts.get("passed", 0), len(rows)),
        "preference_counts": dict(preference_counts),
        "requirements_coverage_counts": dict(coverage_counts),
        "rule_summary": {
            rule_id: dict(counts) for rule_id, counts in sorted(rule_summary.items())
        },
    }


def audit_extraction_coverage(
    cases: Iterable[ReportCase],
    expectations: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """按对话轮次检查应展示的结构化约束是否出现。"""

    first_by_turn: dict[str, ReportCase] = {}
    for case in cases:
        first_by_turn.setdefault(f"{case.dialogue_id}:{case.turn_number}", case)
    rows: list[dict[str, Any]] = []
    for key, expected in sorted(expectations.items(), key=lambda item: tuple(map(int, item[0].split(":")))):
        case = first_by_turn.get(key)
        if case is None:
            rows.append({"dialogue_turn": key, "status": "missing_turn", "expected": [], "missing": ["整轮缺失"], "unsupported": expected.get("unsupported", [])})
            continue
        displayed = f"{case.hard_text}；{case.soft_text}"
        fragments = list(expected.get("display_fragments", []))
        missing = [fragment for fragment in fragments if fragment not in displayed]
        rows.append(
            {
                "dialogue_turn": key,
                "status": "fail" if missing else "pass",
                "expected": fragments,
                "missing": missing,
                "unsupported": list(expected.get("unsupported", [])),
                "displayed": displayed,
            }
        )
    return rows


def _parse_turn_rows(
    dialogue_id: int,
    turn_number: int,
    user_message: str,
    block: str,
) -> list[ReportCase]:
    row_pattern = re.compile(
        r"<tr><td>(?P<profile>\d+)</td>"
        r"<td>(?P<hard>.*?)</td>"
        r"<td>(?P<soft>.*?)</td>"
        r"<td><span class='status (?P<status_class>[^']+)'>(?P<status_label>.*?)</span>"
        r"(?P<status_extra>.*?)</td><td>(?P<answer_cell>.*?)</td></tr>",
        flags=re.DOTALL,
    )
    rows: list[ReportCase] = []
    for match in row_pattern.finditer(block):
        answer_match = re.search(
            r"<pre class='answer'>(.*?)</pre>",
            match.group("answer_cell"),
            flags=re.DOTALL,
        )
        answer = html.unescape(answer_match.group(1)).strip() if answer_match else ""
        selected = tuple(
            item.strip()
            for item in re.findall(r"(?m)^\d+\.\s+([^\r\n]+?)\s*$", answer)
        )
        diner_match = re.search(r"(\d+)人份菜单", answer)
        rows.append(
            ReportCase(
                dialogue_id=dialogue_id,
                turn_number=turn_number,
                user_message=user_message,
                profile_id=int(match.group("profile")),
                hard_text=_plain_text(match.group("hard")),
                soft_text=_plain_text(match.group("soft")),
                generation_status=_normalize_generation_status(
                    match.group("status_class"), match.group("status_label")
                ),
                answer_text=answer,
                selected_recipes=selected,
                answer_diner_count=int(diner_match.group(1)) if diner_match else None,
            )
        )
    if len(rows) != 50:
        raise AcceptanceAuditError(
            f"对话{dialogue_id}第{turn_number}轮应有50份档案，实际为{len(rows)}"
        )
    return rows


def _normalize_generation_status(css_class: str, label: str) -> str:
    plain_label = _plain_text(label)
    if css_class in {"generated", "quality-warning"} or plain_label.startswith("生成推荐"):
        return "recommended"
    if css_class == "empty" or "空候选" in plain_label:
        return "empty_candidate"
    if css_class == "infeasible" or "规划无解" in plain_label:
        return "planning_infeasible"
    return plain_label or css_class


def _audit_global_constraints(
    rules: list[dict[str, Any]],
    case: ReportCase,
    expected: Mapping[str, Any],
    profile: Mapping[str, Any],
    recipes: list[RecipeAuditRecord],
    concept_members: Mapping[str, frozenset[str]],
    inconsistent_recipes: frozenset[str],
) -> None:
    meal_period = expected.get("meal_period")
    if meal_period:
        violating = [recipe.name for recipe in recipes if meal_period not in recipe.tags]
        _append_rule(rules, "hard.meal_period", "hard_constraint", "fail" if violating else "pass", meal_period, violating or "全部命中", [recipe.name for recipe in recipes])

    max_difficulty = expected.get("max_difficulty")
    if max_difficulty:
        violating = [recipe.name for recipe in recipes if DIFFICULTY_RANK.get(recipe.difficulty, 99) > DIFFICULTY_RANK[max_difficulty]]
        _append_rule(rules, "hard.max_difficulty", "hard_constraint", "fail" if violating else "pass", f"≤{max_difficulty}", violating or "全部满足", {recipe.name: recipe.difficulty for recipe in recipes})

    max_time = expected.get("max_total_time_minutes")
    if max_time is not None:
        violating = [recipe.name for recipe in recipes if recipe.total_time_minutes > int(max_time)]
        _append_rule(rules, "hard.max_time", "hard_constraint", "fail" if violating else "pass", f"每道菜≤{max_time}分钟", violating or "全部满足", {recipe.name: recipe.total_time_minutes for recipe in recipes})

    diner_count = expected.get("diner_count")
    if diner_count is not None:
        _append_rule(rules, "hard.diner_count", "hard_constraint", "pass" if case.answer_diner_count == diner_count else "fail", diner_count, case.answer_diner_count, "回答标题")

    total_count = expected.get("total_dish_count")
    if total_count is not None:
        _append_rule(rules, "hard.total_dish_count", "hard_constraint", "pass" if len(recipes) == total_count else "fail", total_count, len(recipes), list(case.selected_recipes))

    allergens = list(profile.get("allergens", []))
    for allergen in allergens:
        excluded = concept_members.get(allergen, frozenset({allergen}))
        violating = {
            recipe.name: sorted(recipe.ingredients & excluded)
            for recipe in recipes
            if recipe.ingredients & excluded
        }
        status = "not_auditable" if any(recipe.name in inconsistent_recipes for recipe in recipes) else ("fail" if violating else "pass")
        _append_rule(rules, "hard.allergen", "hard_constraint", status, f"排除{allergen}", violating or "无命中", {recipe.name: sorted(recipe.ingredients) for recipe in recipes})

    _, negative_tastes = parse_profile_tastes(str(profile.get("taste_preference", "")))
    negative_tastes.update(expected.get("negative_tastes", []))
    for taste in sorted(negative_tastes):
        violating = [recipe.name for recipe in recipes if taste in recipe.tags]
        _append_rule(rules, "hard.negative_taste", "hard_constraint", "fail" if violating else "pass", f"不{taste}", violating or "无命中", {recipe.name: sorted(recipe.tags) for recipe in recipes})

    available = frozenset(expected.get("available_ingredients", []))
    if available:
        violating = {
            recipe.name: sorted(recipe.core_ingredients - available)
            for recipe in recipes
            if recipe.core_ingredients - available
        }
        status = "not_auditable" if any(recipe.name in inconsistent_recipes for recipe in recipes) else ("fail" if violating else "pass")
        _append_rule(rules, "hard.available_ingredients", "hard_constraint", status, sorted(available), violating or "全部核心食材可用", {recipe.name: sorted(recipe.core_ingredients) for recipe in recipes})

    duplicates = [name for name, count in Counter(case.selected_recipes).items() if count > 1]
    _append_rule(rules, "hard.unique_recipe", "hard_constraint", "fail" if duplicates else "pass", "菜名不重复", duplicates or "无重复", list(case.selected_recipes))


def _audit_dish_groups(
    rules: list[dict[str, Any]],
    expected: Mapping[str, Any],
    recipes: list[RecipeAuditRecord],
    concept_members: Mapping[str, frozenset[str]],
) -> None:
    groups = list(expected.get("dish_groups", []))
    if not groups:
        return
    slots: list[Mapping[str, Any]] = []
    if len(groups) == 1 and groups[0].get("count") is None:
        slots = [groups[0]] * len(recipes)
    else:
        for group in groups:
            count = group.get("count")
            slots.extend([group] * (1 if count is None else int(count)))
    if len(slots) != len(recipes):
        _append_rule(rules, "hard.dish_group_count", "hard_constraint", "fail", len(slots), len(recipes), [recipe.name for recipe in recipes])
        return
    assignment = _find_assignment(recipes, slots, concept_members)
    _append_rule(
        rules,
        "hard.dish_groups",
        "hard_constraint",
        "pass" if assignment is not None else "fail",
        groups,
        assignment if assignment is not None else [recipe.name for recipe in recipes],
        "按菜品类型、负向口味和必需食材进行一对一匹配",
    )


def _audit_preferences(
    rules: list[dict[str, Any]],
    expected: Mapping[str, Any],
    profile: Mapping[str, Any],
    recipes: list[RecipeAuditRecord],
    concept_members: Mapping[str, frozenset[str]],
) -> None:
    """统计正向口味、菜系、功效和人群的匹配情况，不计入硬约束。"""

    profile_positive, _ = parse_profile_tastes(str(profile.get("taste_preference", "")))
    preference_groups = (
        ("profile_positive_taste", profile_positive),
        ("positive_taste", set(expected.get("positive_tastes", []))),
        ("cuisine", set(expected.get("cuisines", []))),
        ("effect", set(expected.get("effects", []))),
        ("population", set(expected.get("populations", []))),
    )
    for rule_suffix, tags in preference_groups:
        for tag in sorted(tags):
            matched = [recipe.name for recipe in recipes if tag in recipe.tags]
            _append_rule(
                rules,
                f"preference.{rule_suffix}",
                "preference",
                "pass" if matched else "fail",
                f"菜单至少一道菜命中{tag}",
                matched or "无命中",
                {recipe.name: sorted(recipe.tags) for recipe in recipes},
            )

    groups = list(expected.get("dish_groups", []))
    if groups and any(group.get("positive_tastes") for group in groups):
        if len(groups) == 1 and groups[0].get("count") is None:
            slots: list[Mapping[str, Any]] = [groups[0]] * len(recipes)
        else:
            slots = []
            for group in groups:
                count = group.get("count")
                slots.extend([group] * (1 if count is None else int(count)))
        if len(slots) == len(recipes):
            assignment = _find_assignment(
                recipes,
                slots,
                concept_members,
                enforce_positive_tastes=True,
            )
            _append_rule(
                rules,
                "preference.dish_group_positive_taste",
                "preference",
                "pass" if assignment is not None else "fail",
                "正向口味按需求组匹配",
                assignment if assignment is not None else [recipe.name for recipe in recipes],
                groups,
            )


def _find_assignment(
    recipes: list[RecipeAuditRecord],
    slots: list[Mapping[str, Any]],
    concept_members: Mapping[str, frozenset[str]],
    *,
    enforce_positive_tastes: bool = False,
) -> list[dict[str, Any]] | None:
    used: set[int] = set()
    assignment: list[dict[str, Any]] = []

    def visit(slot_index: int) -> bool:
        if slot_index == len(slots):
            return True
        for recipe_index, recipe in enumerate(recipes):
            if recipe_index in used or not _recipe_meets_group(
                recipe,
                slots[slot_index],
                concept_members,
                enforce_positive_tastes=enforce_positive_tastes,
            ):
                continue
            used.add(recipe_index)
            assignment.append({"slot": slot_index, "recipe": recipe.name})
            if visit(slot_index + 1):
                return True
            assignment.pop()
            used.remove(recipe_index)
        return False

    return list(assignment) if visit(0) else None


def _recipe_meets_group(
    recipe: RecipeAuditRecord,
    group: Mapping[str, Any],
    concept_members: Mapping[str, frozenset[str]],
    *,
    enforce_positive_tastes: bool = False,
) -> bool:
    dish_type = group.get("dish_type")
    if dish_type and recipe.dish_type != dish_type:
        return False
    if any(taste in recipe.tags for taste in group.get("negative_tastes", [])):
        return False
    if enforce_positive_tastes and not all(
        taste in recipe.tags for taste in group.get("positive_tastes", [])
    ):
        return False
    return all(
        _requirement_group_matches(recipe, requirement, concept_members)
        for requirement in group.get("required_ingredient_groups", [])
    )


def _requirement_group_matches(
    recipe: RecipeAuditRecord,
    requirement: Mapping[str, Any],
    concept_members: Mapping[str, frozenset[str]],
) -> bool:
    matches = [
        _ingredient_requirement_matches(recipe, item, concept_members)
        for item in requirement.get("items", [])
    ]
    return all(matches) if requirement.get("match") == "all" else any(matches)


def _ingredient_requirement_matches(
    recipe: RecipeAuditRecord,
    item: Mapping[str, Any],
    concept_members: Mapping[str, frozenset[str]],
) -> bool:
    kind = item.get("kind")
    value = str(item.get("value", ""))
    if kind == "ingredient":
        return value in recipe.ingredients
    if kind == "category":
        return value in recipe.ingredient_categories.values()
    if kind == "concept":
        return bool(recipe.ingredients & concept_members.get(value, frozenset()))
    if kind == "name_contains":
        return any(value in ingredient for ingredient in recipe.ingredients)
    return False


def parse_profile_tastes(value: str) -> tuple[set[str], set[str]]:
    """解析健康档案中的受控口味文本，返回正向与负向标签。"""

    compact = re.sub(r"[、，,\s]", "", value)
    if compact in {"", "无", "忽略", "不管"}:
        return set(), set()
    positive: set[str] = set()
    negative: set[str] = set()
    position = 0
    while position < len(compact):
        for token, label, enabled in PROFILE_TASTE_TOKENS:
            if compact.startswith(token, position):
                (positive if enabled else negative).add(label)
                position += len(token)
                break
        else:
            raise AcceptanceAuditError(f"档案口味出现未识别内容：{value}")
    return positive, negative


def _append_rule(
    rules: list[dict[str, Any]],
    rule_id: str,
    category: str,
    status: str,
    expected: Any,
    actual: Any,
    evidence: Any,
) -> None:
    if status not in AUDIT_STATUSES:
        raise AcceptanceAuditError(f"非法验收状态：{status}")
    rules.append(
        {
            "rule_id": rule_id,
            "category": category,
            "status": status,
            "expected": expected,
            "actual": actual,
            "evidence": evidence,
        }
    )


def _aggregate_status(rules: list[dict[str, Any]], category: str) -> str:
    statuses = [rule["status"] for rule in rules if rule["category"] == category]
    if not statuses:
        return "not_applicable"
    if "fail" in statuses:
        return "fail"
    if "not_auditable" in statuses:
        return "not_auditable"
    return "pass"


def _compare_field(
    issues: list[dict[str, Any]],
    recipe_name: str,
    field: str,
    expected: Any,
    actual: Any,
    source: str,
) -> None:
    if expected != actual:
        issues.append(_consistency_issue(recipe_name, field, source, expected, actual))


def _consistency_issue(
    recipe_name: str,
    field: str,
    source: str,
    expected: Any,
    actual: Any,
) -> dict[str, Any]:
    return {
        "recipe_name": recipe_name,
        "field": field,
        "source": source,
        "expected": expected,
        "actual": actual,
    }


def _plain_text(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator * 100, 2) if denominator else None


__all__ = [
    "AcceptanceAuditError",
    "RecipeAuditRecord",
    "ReportCase",
    "audit_extraction_coverage",
    "audit_report_case",
    "compare_catalogs",
    "parse_delivery_report",
    "parse_profile_tastes",
    "summarize_audits",
]
