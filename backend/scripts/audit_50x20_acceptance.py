from __future__ import annotations

"""对冻结的50×20推荐结果执行独立、只读的验收复核。"""

import argparse
import csv
import hashlib
import html
import json
import sys
import tomllib
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from neo4j import Driver
from sqlalchemy import Engine, text

from backend.core.dish_filtering_contract import (
    ALLERGEN_CONCEPT_MEMBERS,
    AUXILIARY_INGREDIENTS,
    TAG_TO_GROUP,
)
from backend.core.recipe_difficulty import derive_recipe_difficulty
from backend.infrastructure.database.database import create_database_engine
from backend.infrastructure.graph.neo4j import create_neo4j_driver
from backend.services.acceptance_audit import (
    AcceptanceAuditError,
    RecipeAuditRecord,
    ReportCase,
    audit_extraction_coverage,
    audit_report_case,
    compare_catalogs,
    parse_delivery_report,
    summarize_audits,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES_PATH = REPO_ROOT / "tests" / ".pytest-tmp" / "spec10_50x20_unified_cases.json"
DEFAULT_REPORT_PATH = REPO_ROOT / "docs" / "交付" / "测试报告_50x20_v1.html"
DEFAULT_EXPECTATIONS_PATH = (
    REPO_ROOT
    / "backend"
    / "resources"
    / "acceptance"
    / "spec10_expected_constraints.json"
)
DEFAULT_RECIPES_PATH = (
    REPO_ROOT / "datas" / "processed" / "Recipes" / "RecipeComplete.json"
)
DEFAULT_INGREDIENTS_PATH = (
    REPO_ROOT
    / "datas"
    / "processed"
    / "Ingredients"
    / "Ingredients2Nutrition.csv"
)
DEFAULT_USERS_PATH = (
    REPO_ROOT
    / "datas"
    / "processed"
    / "users"
    / "50个用户健康档案_归一化.json"
)
DEFAULT_OUTPUT_JSON = (
    REPO_ROOT / "tests" / ".pytest-tmp" / "spec10_50x20_acceptance_audit.json"
)
DEFAULT_OUTPUT_HTML = REPO_ROOT / "docs" / "交付" / "测试报告_50x20_验收版.html"

NUTRIENT_FIELDS = (
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
EXPECTED_PROFILE_COUNT = 50
EXPECTED_RECIPE_COUNT = 1912
EXPECTED_REPORT_CASE_COUNT = 1450
EXPECTED_FINAL_CASE_COUNT = 1000


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="不调用LLM，以正式数据、PostgreSQL和Neo4j复核冻结的50×20结果。"
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--report-source", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--expectations", type=Path, default=DEFAULT_EXPECTATIONS_PATH)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-html", type=Path, default=DEFAULT_OUTPUT_HTML)
    parser.add_argument(
        "--fail-on-violation",
        action="store_true",
        help="发现硬约束或真实性违规时返回非零退出码",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run_audit(args)
        _write_json(args.output_json, result)
        _write_text(args.output_html, render_html(result))
    except AcceptanceAuditError as exc:
        print(f"验收器执行失败：{exc}", file=sys.stderr)
        return 2

    summary = result["summary"]
    print(
        "验收完成："
        f"逐轮{summary['total']}条，生成{summary['generation_counts'].get('recommended', 0)}条，"
        f"严格通过{summary['strict_counts'].get('passed', 0)}条。"
    )
    print(f"JSON：{args.output_json.resolve()}")
    print(f"HTML：{args.output_html.resolve()}")
    has_violation = bool(
        summary["strict_counts"].get("failed", 0)
        or result["data_consistency"]["issue_count"]
        or result["extraction_coverage"]["failed_turn_count"]
        or result["final_case_traceability"]["violation_count"]
    )
    return 1 if args.fail_on_violation and has_violation else 0


def run_audit(args: argparse.Namespace) -> dict[str, Any]:
    for path in (
        args.cases,
        args.report_source,
        args.expectations,
        DEFAULT_RECIPES_PATH,
        DEFAULT_INGREDIENTS_PATH,
        DEFAULT_USERS_PATH,
    ):
        if not path.is_file():
            raise AcceptanceAuditError(f"缺少验收输入文件：{path}")

    report_cases = parse_delivery_report(args.report_source)
    if len(report_cases) != EXPECTED_REPORT_CASE_COUNT:
        raise AcceptanceAuditError(
            f"逐轮验收输入应为{EXPECTED_REPORT_CASE_COUNT}条，实际为{len(report_cases)}条"
        )
    frozen = _load_json(args.cases)
    frozen_cases = frozen.get("cases")
    if not isinstance(frozen_cases, list) or len(frozen_cases) != EXPECTED_FINAL_CASE_COUNT:
        raise AcceptanceAuditError(
            "冻结JSON应包含20组对话各50份档案的最终轮结果，"
            f"实际为{len(frozen_cases) if isinstance(frozen_cases, list) else '非法结构'}条"
        )
    expectations = _load_json(args.expectations)
    if not isinstance(expectations, dict) or len(expectations) != 29:
        raise AcceptanceAuditError("人工预期约束清单应包含29个对话轮次")

    reference = _load_reference_catalog(DEFAULT_RECIPES_PATH, DEFAULT_INGREDIENTS_PATH)
    file_profiles = _load_file_profiles(DEFAULT_USERS_PATH)
    config = _load_project_config()
    database_url = _required_config(config, "database", "url")
    neo4j_config = config.get("neo4j")
    if not isinstance(neo4j_config, dict):
        raise AcceptanceAuditError("pyproject.toml缺少tool.mealagent.neo4j配置")

    engine = create_database_engine(database_url)
    driver = create_neo4j_driver(
        _required_mapping_value(neo4j_config, "uri", "Neo4j"),
        _required_mapping_value(neo4j_config, "user", "Neo4j"),
        _required_mapping_value(neo4j_config, "password", "Neo4j"),
    )
    try:
        postgres, pg_profiles, pg_counts = _load_postgres(engine)
        graph, graph_concepts, graph_counts = _load_graph(driver)
    except Exception as exc:
        raise AcceptanceAuditError(
            "数据库只读检查失败，请先执行 docker compose up -d postgres neo4j"
        ) from exc
    finally:
        driver.close()
        engine.dispose()

    _validate_counts(reference, file_profiles, postgres, pg_profiles, graph, pg_counts, graph_counts)
    profile_issues = _compare_profiles(file_profiles, pg_profiles)
    catalog_issues = compare_catalogs(
        reference,
        postgres,
        graph,
        graph_tag_names=frozenset(TAG_TO_GROUP),
    )
    available_ingredient_names = frozenset(
        ingredient
        for recipe in reference.values()
        for ingredient in recipe.ingredients
    )
    concept_issues = _compare_concepts(graph_concepts, available_ingredient_names)
    consistency_issues = catalog_issues + profile_issues + concept_issues
    inconsistent_recipes = frozenset(
        issue["recipe_name"]
        for issue in catalog_issues
        if issue.get("recipe_name") in reference
    )

    audits: list[dict[str, Any]] = []
    for case in report_cases:
        key = f"{case.dialogue_id}:{case.turn_number}"
        expected = expectations.get(key)
        if not isinstance(expected, dict):
            raise AcceptanceAuditError(f"人工预期约束缺少对话轮次：{key}")
        profile = pg_profiles.get(case.profile_id)
        if profile is None:
            raise AcceptanceAuditError(f"PostgreSQL缺少用户档案：{case.profile_id}")
        audits.append(
            audit_report_case(
                case,
                expected,
                profile,
                reference,
                postgres,
                graph,
                graph_concepts,
                inconsistent_recipes,
            )
        )

    extraction_rows = audit_extraction_coverage(report_cases, expectations)
    final_traceability = _audit_final_cases(
        frozen_cases,
        report_cases,
        reference,
        postgres,
        graph,
    )
    summary = summarize_audits(audits)
    summary["dialogues"] = _summarize_dialogues(audits)

    return {
        "audit_version": "1.0",
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "method": {
            "llm_called": False,
            "database_access": "PostgreSQL只读事务；Neo4j execute_read",
            "report_scope": "1450条档案×轮次结果",
            "frozen_json_scope": "1000条档案×对话最终轮结果",
            "strict_definition": "已生成推荐，且全部可审计硬约束与菜谱真实性均通过",
            "time_rule": "逐道菜的制作时间下界不超过限制；整桌并行工期未自动验收",
        },
        "sources": {
            "report": _file_metadata(args.report_source),
            "frozen_cases": _file_metadata(args.cases),
            "expectations": _file_metadata(args.expectations),
            "recipes": _file_metadata(DEFAULT_RECIPES_PATH),
            "ingredients": _file_metadata(DEFAULT_INGREDIENTS_PATH),
            "profiles": _file_metadata(DEFAULT_USERS_PATH),
        },
        "database_counts": {"postgresql": pg_counts, "neo4j": graph_counts},
        "data_consistency": {
            "status": "pass" if not consistency_issues else "fail",
            "issue_count": len(consistency_issues),
            "issues": consistency_issues,
        },
        "summary": summary,
        "extraction_coverage": {
            "turn_count": len(extraction_rows),
            "failed_turn_count": sum(row["status"] != "pass" for row in extraction_rows),
            "unsupported_requirement_count": sum(len(row["unsupported"]) for row in extraction_rows),
            "rows": extraction_rows,
        },
        "final_case_traceability": final_traceability,
        "case_audits": audits,
    }


def _load_reference_catalog(
    recipes_path: Path,
    ingredients_path: Path,
) -> dict[str, RecipeAuditRecord]:
    ingredient_categories: dict[str, str | None] = {}
    try:
        with ingredients_path.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                name = str(row.get("标准食材名", "")).strip()
                if not name:
                    raise AcceptanceAuditError("食材营养CSV存在空标准食材名")
                ingredient_categories[name] = str(row.get("分类", "")).strip() or None
    except OSError as exc:
        raise AcceptanceAuditError(f"无法读取食材营养CSV：{ingredients_path}") from exc

    rows = _load_json(recipes_path)
    if not isinstance(rows, list):
        raise AcceptanceAuditError("RecipeComplete.json顶层必须是数组")
    result: dict[str, RecipeAuditRecord] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise AcceptanceAuditError("RecipeComplete.json存在非对象菜谱")
        name = str(row.get("name", "")).strip()
        ingredients = frozenset(str(item) for item in row.get("ingredients", {}))
        unknown = sorted(ingredients - ingredient_categories.keys())
        if unknown:
            raise AcceptanceAuditError(f"菜谱{name}存在未归一化食材：{unknown}")
        total_time = int(row["total_time_lower_bound_minutes"])
        difficulty = derive_recipe_difficulty(
            total_time_minutes=total_time,
            atomic_step_count=len(row.get("atomic_steps", [])),
            ingredient_count=len(ingredients),
        )
        result[name] = RecipeAuditRecord(
            name=name,
            is_recommendable=bool(row["is_recommendable"]),
            tags=frozenset(str(item) for item in row.get("labels", [])),
            difficulty=difficulty,
            total_time_minutes=total_time,
            dish_type=row.get("dish_type"),
            ingredients=ingredients,
            core_ingredients=frozenset(ingredients - AUXILIARY_INGREDIENTS),
            ingredient_categories={item: ingredient_categories[item] for item in ingredients},
            nutrition={},
        )
    if len(result) != len(rows):
        raise AcceptanceAuditError("RecipeComplete.json存在重复菜名")
    return result


def _load_file_profiles(path: Path) -> dict[int, dict[str, Any]]:
    rows = _load_json(path)
    if not isinstance(rows, list):
        raise AcceptanceAuditError("归一化健康档案顶层必须是数组")
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        profile_id = int(row["id"])
        result[profile_id] = {
            "allergens": list(row.get("过敏食材", [])),
            "taste_preference": str(row.get("口味偏好", "")),
        }
    if len(result) != len(rows):
        raise AcceptanceAuditError("归一化健康档案存在重复ID")
    return result


def _load_postgres(
    engine: Engine,
) -> tuple[dict[str, RecipeAuditRecord], dict[int, dict[str, Any]], dict[str, int]]:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            connection.execute(text("SELECT 1"))
            recipe_rows = connection.execute(
                text(
                    """
                    SELECT r.id, r.name, r.is_recommendable,
                           r.total_time_lower_bound_minutes, r.dish_type,
                           r.labels, r.difficulty,
                           n.energy_kcal, n.protein_g, n.fat_g,
                           n.carbohydrate_g, n.fiber_g, n.sodium_mg,
                           n.calcium_mg, n.iron_mg, n.cholesterol_mg
                    FROM recipes AS r
                    LEFT JOIN recipe_nutrition AS n ON n.recipe_id = r.id
                    ORDER BY r.id
                    """
                )
            ).mappings().all()
            relation_rows = connection.execute(
                text(
                    """
                    SELECT r.name AS recipe_name, i.name AS ingredient_name,
                           i.category AS ingredient_category
                    FROM recipe_ingredients AS ri
                    JOIN recipes AS r ON r.id = ri.recipe_id
                    JOIN ingredients AS i ON i.id = ri.ingredient_id
                    ORDER BY r.id, i.id
                    """
                )
            ).mappings().all()
            profile_rows = connection.execute(
                text(
                    "SELECT id, allergens, taste_preference "
                    "FROM user_profiles ORDER BY id"
                )
            ).mappings().all()
            counts = {
                "profiles": _scalar_count(connection, "user_profiles"),
                "recipes": _scalar_count(connection, "recipes"),
                "recipe_nutrition": _scalar_count(connection, "recipe_nutrition"),
                "ingredients_all": _scalar_count(connection, "ingredients"),
                "recipe_ingredient_relations": _scalar_count(connection, "recipe_ingredients"),
                "ingredients_used": int(
                    connection.execute(
                        text("SELECT COUNT(DISTINCT ingredient_id) FROM recipe_ingredients")
                    ).scalar_one()
                ),
            }
        finally:
            transaction.rollback()

    ingredient_names: dict[str, set[str]] = defaultdict(set)
    ingredient_categories: dict[str, dict[str, str | None]] = defaultdict(dict)
    for row in relation_rows:
        recipe_name = str(row["recipe_name"])
        ingredient_name = str(row["ingredient_name"])
        ingredient_names[recipe_name].add(ingredient_name)
        ingredient_categories[recipe_name][ingredient_name] = row["ingredient_category"]

    catalog: dict[str, RecipeAuditRecord] = {}
    for row in recipe_rows:
        name = str(row["name"])
        ingredients = frozenset(ingredient_names[name])
        nutrition = {
            field: Decimal(row[field])
            for field in NUTRIENT_FIELDS
            if row[field] is not None
        }
        catalog[name] = RecipeAuditRecord(
            name=name,
            is_recommendable=bool(row["is_recommendable"]),
            tags=frozenset(str(item) for item in row["labels"]),
            difficulty=str(row["difficulty"]),
            total_time_minutes=int(row["total_time_lower_bound_minutes"]),
            dish_type=row["dish_type"],
            ingredients=ingredients,
            core_ingredients=frozenset(ingredients - AUXILIARY_INGREDIENTS),
            ingredient_categories=ingredient_categories[name],
            nutrition=nutrition,
        )
    profiles = {
        int(row["id"]): {
            "allergens": list(row["allergens"]),
            "taste_preference": str(row["taste_preference"]),
        }
        for row in profile_rows
    }
    return catalog, profiles, counts


def _load_graph(
    driver: Driver,
) -> tuple[dict[str, RecipeAuditRecord], dict[str, frozenset[str]], dict[str, int]]:
    driver.verify_connectivity()
    with driver.session() as session:
        recipe_rows = session.execute_read(
            lambda transaction: [
                row.data()
                for row in transaction.run(
                    """
                    MATCH (r:Recipe)
                    RETURN r.name AS name,
                           r.is_recommendable AS is_recommendable,
                           r.tags AS tags,
                           r.difficulty AS difficulty,
                           r.total_time_lower_bound_minutes AS total_time,
                           r.dish_type AS dish_type
                    ORDER BY r.name
                    """
                )
            ]
        )
        relation_rows = session.execute_read(
            lambda transaction: [
                row.data()
                for row in transaction.run(
                    """
                    MATCH (i:Ingredient)-[:part_of]->(r:Recipe)
                    RETURN r.name AS recipe_name, i.name AS ingredient_name,
                           i.category AS ingredient_category,
                           i.is_core_ingredient AS is_core
                    ORDER BY r.name, i.name
                    """
                )
            ]
        )
        concept_rows = session.execute_read(
            lambda transaction: [
                row.data()
                for row in transaction.run(
                    """
                    MATCH (i:Ingredient)-[:is_a]->(c:Concept)
                    RETURN c.name AS concept_name, i.name AS ingredient_name
                    ORDER BY c.name, i.name
                    """
                )
            ]
        )
        count_row = session.execute_read(
            lambda transaction: transaction.run(
                """
                MATCH (r:Recipe) WITH count(r) AS recipes
                MATCH (i:Ingredient) WITH recipes, count(i) AS ingredients
                MATCH (c:Concept) WITH recipes, ingredients, count(c) AS concepts
                MATCH (:Ingredient)-[p:part_of]->(:Recipe)
                RETURN recipes, ingredients, concepts, count(p) AS recipe_ingredient_relations
                """
            ).single(strict=True).data()
        )

    names: dict[str, set[str]] = defaultdict(set)
    core_names: dict[str, set[str]] = defaultdict(set)
    categories: dict[str, dict[str, str | None]] = defaultdict(dict)
    for row in relation_rows:
        recipe_name = str(row["recipe_name"])
        ingredient_name = str(row["ingredient_name"])
        names[recipe_name].add(ingredient_name)
        categories[recipe_name][ingredient_name] = row["ingredient_category"]
        if bool(row["is_core"]):
            core_names[recipe_name].add(ingredient_name)
    catalog = {
        str(row["name"]): RecipeAuditRecord(
            name=str(row["name"]),
            is_recommendable=bool(row["is_recommendable"]),
            tags=frozenset(str(item) for item in (row["tags"] or [])),
            difficulty=str(row["difficulty"]),
            total_time_minutes=int(row["total_time"]),
            dish_type=row["dish_type"],
            ingredients=frozenset(names[str(row["name"])]),
            core_ingredients=frozenset(core_names[str(row["name"])]),
            ingredient_categories=categories[str(row["name"])],
            nutrition={},
        )
        for row in recipe_rows
    }
    concept_members: dict[str, set[str]] = defaultdict(set)
    for row in concept_rows:
        concept_members[str(row["concept_name"])].add(str(row["ingredient_name"]))
    return (
        catalog,
        {name: frozenset(values) for name, values in concept_members.items()},
        {key: int(value) for key, value in count_row.items()},
    )


def _validate_counts(
    reference: Mapping[str, Any],
    file_profiles: Mapping[int, Any],
    postgres: Mapping[str, Any],
    pg_profiles: Mapping[int, Any],
    graph: Mapping[str, Any],
    pg_counts: Mapping[str, int],
    graph_counts: Mapping[str, int],
) -> None:
    actual = {
        "正式JSON菜谱": len(reference),
        "正式JSON档案": len(file_profiles),
        "PostgreSQL菜谱": len(postgres),
        "PostgreSQL档案": len(pg_profiles),
        "PostgreSQL菜谱营养": pg_counts["recipe_nutrition"],
        "Neo4j菜谱": len(graph),
    }
    expected = {
        "正式JSON菜谱": EXPECTED_RECIPE_COUNT,
        "正式JSON档案": EXPECTED_PROFILE_COUNT,
        "PostgreSQL菜谱": EXPECTED_RECIPE_COUNT,
        "PostgreSQL档案": EXPECTED_PROFILE_COUNT,
        "PostgreSQL菜谱营养": EXPECTED_RECIPE_COUNT,
        "Neo4j菜谱": EXPECTED_RECIPE_COUNT,
    }
    mismatches = {
        key: {"expected": expected[key], "actual": value}
        for key, value in actual.items()
        if value != expected[key]
    }
    if graph_counts.get("recipes") != EXPECTED_RECIPE_COUNT:
        mismatches["Neo4j菜谱计数查询"] = {
            "expected": EXPECTED_RECIPE_COUNT,
            "actual": graph_counts.get("recipes"),
        }
    if mismatches:
        raise AcceptanceAuditError(
            f"数据源数量不完整，验收已停止：{json.dumps(mismatches, ensure_ascii=False)}"
        )


def _compare_profiles(
    expected: Mapping[int, Mapping[str, Any]],
    actual: Mapping[int, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for profile_id in sorted(set(expected) | set(actual)):
        left = expected.get(profile_id)
        right = actual.get(profile_id)
        if left != right:
            issues.append(
                {
                    "recipe_name": f"[用户档案]{profile_id}",
                    "field": "allergens_and_taste_preference",
                    "source": "PostgreSQL",
                    "expected": left,
                    "actual": right,
                }
            )
    return issues


def _compare_concepts(
    graph_concepts: Mapping[str, frozenset[str]],
    available_ingredient_names: frozenset[str],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for concept, members in ALLERGEN_CONCEPT_MEMBERS.items():
        expected = sorted(set(members) & available_ingredient_names)
        actual = sorted(graph_concepts.get(concept, frozenset()))
        if expected != actual:
            issues.append(
                {
                    "recipe_name": f"[过敏概念]{concept}",
                    "field": "concept_members",
                    "source": "Neo4j",
                    "expected": expected,
                    "actual": actual,
                }
            )
    return issues


def _audit_final_cases(
    frozen_cases: list[dict[str, Any]],
    report_cases: list[ReportCase],
    reference: Mapping[str, RecipeAuditRecord],
    postgres: Mapping[str, RecipeAuditRecord],
    graph: Mapping[str, RecipeAuditRecord],
) -> dict[str, Any]:
    dialogue_ids = sorted({int(case["dialogue_id"]) for case in frozen_cases})
    final_turn_by_dialogue = {
        dialogue_id: max(
            case.turn_number for case in report_cases if case.dialogue_id == dialogue_id
        )
        for dialogue_id in dialogue_ids
    }
    report_index = {
        (case.dialogue_id, case.turn_number, case.profile_id): case
        for case in report_cases
    }
    status_counts: Counter[str] = Counter()
    violations: list[dict[str, Any]] = []
    cross_artifact_differences: list[dict[str, Any]] = []
    checked_recommendations = 0
    checked_dishes = 0
    for case in frozen_cases:
        dialogue_id = int(case["dialogue_id"])
        profile_id = int(case["profile_id"])
        status = str(case["status"])
        status_counts[status] += 1
        if status != "recommended":
            continue
        checked_recommendations += 1
        generation = case.get("generation_result", {})
        planning = generation.get("menu_planning_result", {})
        selected = planning.get("selected_dishes", [])
        nested_names = [str(item.get("recipe_name", "")) for item in selected]
        top_names = [str(item) for item in case.get("selected_recipes", [])]
        _record_trace_violation(
            violations,
            dialogue_id,
            profile_id,
            "selected_names_consistent",
            top_names == nested_names,
            top_names,
            nested_names,
        )
        candidate_rows = generation.get("dish_filtering_audit", {}).get(
            "selected_candidates", []
        )
        candidate_pairs = [
            (
                int(item.get("dish_constraint_index", -1)),
                str(item.get("candidate", {}).get("recipe_name", "")),
            )
            for item in candidate_rows
        ]
        selected_pairs = [
            (int(item.get("dish_constraint_index", -1)), str(item.get("recipe_name", "")))
            for item in selected
        ]
        _record_trace_violation(
            violations,
            dialogue_id,
            profile_id,
            "candidate_traceability",
            selected_pairs == candidate_pairs,
            selected_pairs,
            candidate_pairs,
        )
        report_case = report_index.get(
            (dialogue_id, final_turn_by_dialogue[dialogue_id], profile_id)
        )
        report_names = list(report_case.selected_recipes) if report_case else None
        _record_trace_violation(
            cross_artifact_differences,
            dialogue_id,
            profile_id,
            "answer_selected_names_consistent",
            report_names == nested_names,
            nested_names,
            report_names,
        )
        for item in selected:
            checked_dishes += 1
            recipe_name = str(item.get("recipe_name", ""))
            source_presence = {
                "正式JSON": recipe_name in reference,
                "PostgreSQL": recipe_name in postgres,
                "Neo4j": recipe_name in graph,
            }
            _record_trace_violation(
                violations,
                dialogue_id,
                profile_id,
                "three_source_recipe_exists",
                all(source_presence.values()),
                {key: True for key in source_presence},
                source_presence,
                recipe_name=recipe_name,
            )
            pg_recipe = postgres.get(recipe_name)
            if pg_recipe is None:
                continue
            actual_nutrition = item.get("nutrition", {})
            differences = {
                nutrient: {
                    "expected": str(pg_recipe.nutrition[nutrient]),
                    "actual": str(actual_nutrition.get(nutrient)),
                }
                for nutrient in NUTRIENT_FIELDS
                if nutrient not in actual_nutrition
                or Decimal(str(actual_nutrition[nutrient]))
                != pg_recipe.nutrition[nutrient]
            }
            _record_trace_violation(
                violations,
                dialogue_id,
                profile_id,
                "selected_nutrition_matches_postgresql",
                not differences,
                "九项营养与PostgreSQL一致",
                differences or "一致",
                recipe_name=recipe_name,
            )
    return {
        "total": len(frozen_cases),
        "status_counts": dict(status_counts),
        "checked_recommendations": checked_recommendations,
        "checked_dishes": checked_dishes,
        "violation_count": len(violations),
        "violations": violations,
        "cross_artifact_difference_count": len(cross_artifact_differences),
        "cross_artifact_differences": cross_artifact_differences,
        "note": (
            "冻结JSON只保存每组对话最终轮，且与逐轮HTML不是同一份完整执行快照；"
            "二者菜单差异仅作跨产物提示，不判定为真实性违规。"
        ),
    }


def _record_trace_violation(
    violations: list[dict[str, Any]],
    dialogue_id: int,
    profile_id: int,
    rule: str,
    passed: bool,
    expected: Any,
    actual: Any,
    *,
    recipe_name: str | None = None,
) -> None:
    if passed:
        return
    violations.append(
        {
            "dialogue_id": dialogue_id,
            "profile_id": profile_id,
            "recipe_name": recipe_name,
            "rule": rule,
            "expected": expected,
            "actual": actual,
        }
    )


def _summarize_dialogues(
    audits: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for audit in audits:
        rows[int(audit["dialogue_id"])].append(audit)
    result: list[dict[str, Any]] = []
    for dialogue_id, items in sorted(rows.items()):
        generation = Counter(item["generation_status"] for item in items)
        strict = Counter(item["strict_status"] for item in items)
        result.append(
            {
                "dialogue_id": dialogue_id,
                "case_count": len(items),
                "recommended": generation.get("recommended", 0),
                "empty_candidate": generation.get("empty_candidate", 0),
                "planning_infeasible": generation.get("planning_infeasible", 0),
                "strict_passed": strict.get("passed", 0),
                "strict_failed": strict.get("failed", 0),
                "not_auditable": strict.get("not_auditable", 0),
            }
        )
    return result


def render_html(result: Mapping[str, Any]) -> str:
    summary = result["summary"]
    consistency = result["data_consistency"]
    extraction = result["extraction_coverage"]
    traceability = result["final_case_traceability"]
    generation = summary["generation_counts"]
    strict = summary["strict_counts"]
    violation_cases = [
        case
        for case in result["case_audits"]
        if case["strict_status"] in {"failed", "not_auditable"}
    ]
    rule_rows = "".join(
        "<tr>"
        f"<td><code>{_escape(rule_id)}</code></td>"
        f"<td>{counts.get('pass', 0)}</td>"
        f"<td>{counts.get('fail', 0)}</td>"
        f"<td>{counts.get('not_auditable', 0)}</td>"
        "</tr>"
        for rule_id, counts in summary["rule_summary"].items()
    )
    dialogue_rows = "".join(
        "<tr>"
        f"<td>{row['dialogue_id']}</td><td>{row['case_count']}</td>"
        f"<td>{row['recommended']}</td><td>{row['empty_candidate']}</td>"
        f"<td>{row['planning_infeasible']}</td><td>{row['strict_passed']}</td>"
        f"<td>{row['strict_failed']}</td><td>{row['not_auditable']}</td>"
        "</tr>"
        for row in summary["dialogues"]
    )
    extraction_rows = "".join(
        "<tr>"
        f"<td>{_escape(row['dialogue_turn'])}</td>"
        f"<td><span class='pill {row['status']}'>{_escape(row['status'])}</span></td>"
        f"<td>{_escape('；'.join(row['missing']) or '无')}</td>"
        f"<td>{_escape('；'.join(row['unsupported']) or '无')}</td>"
        "</tr>"
        for row in extraction["rows"]
    )
    consistency_details = _render_json_details(
        consistency["issues"], "查看三方数据差异"
    )
    traceability_details = _render_json_details(
        traceability["violations"], "查看最终轮追溯异常"
    )
    cross_artifact_details = _render_json_details(
        traceability["cross_artifact_differences"], "查看跨产物菜单差异"
    )
    violation_details = "".join(
        _render_case_detail(case) for case in violation_cases
    ) or "<p class='ok-text'>没有硬约束或真实性异常。</p>"
    counts = result["database_counts"]
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>50×20独立验收报告</title>
<style>
:root{{--ink:#172033;--muted:#667085;--line:#dbe2ea;--bg:#f4f7fb;--card:#fff;--good:#087443;--bad:#b42318;--warn:#a15c00;--blue:#175cd3}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.65 "Microsoft YaHei",Arial,sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:34px 24px 80px}} h1{{font-size:30px;margin:0 0 6px}} h2{{margin-top:34px;border-bottom:2px solid var(--line);padding-bottom:8px}} h3{{margin:0 0 8px}}
.subtitle{{color:var(--muted)}} .notice{{background:#fff7e8;border-left:4px solid #f79009;padding:12px 16px;margin:20px 0}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:20px 0}} .card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}} .metric{{font-size:27px;font-weight:700}} .label{{color:var(--muted)}}
table{{width:100%;border-collapse:collapse;background:#fff}} th,td{{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}} th{{background:#eef3f8;white-space:nowrap}}
.scroll{{overflow:auto}} .pill{{display:inline-block;border-radius:999px;padding:1px 8px;background:#eef2f6}} .pill.pass,.ok-text{{color:var(--good)}} .pill.fail{{color:var(--bad)}} .pill.not_auditable{{color:var(--warn)}}
details{{background:#fff;border:1px solid var(--line);border-radius:8px;margin:8px 0;padding:10px 12px}} summary{{cursor:pointer;font-weight:600}} pre{{white-space:pre-wrap;word-break:break-word;background:#f8fafc;padding:12px;border-radius:6px;max-height:440px;overflow:auto}}
code{{font-family:Consolas,monospace}} .muted{{color:var(--muted)}} nav a{{margin-right:16px}} ul{{margin-top:5px}}
@media print{{body{{background:#fff}} main{{max-width:none;padding:0}} nav{{display:none}} details{{break-inside:avoid}} details:not([open])>*:not(summary){{display:block}}}}
</style>
</head>
<body><main>
<header><div class="pill">独立验收版</div><h1>50×20推荐结果独立验收报告</h1><div class="subtitle">生成时间：{_escape(result['generated_at'])}｜不调用LLM｜数据库只读</div></header>
<nav><a href="#conclusion">结论</a><a href="#data">数据一致性</a><a href="#rules">规则统计</a><a href="#dialogues">对话汇总</a><a href="#extraction">提取覆盖</a><a href="#details">异常明细</a></nav>
<section id="conclusion"><h2>一、验收结论</h2>
<div class="notice">严格通过不是“生成了回答”。只有成功生成，且硬约束与菜谱真实性均通过，才记为严格通过；当前结构化模型未覆盖的原始语义另列为覆盖风险，不混入硬约束分母。</div>
<div class="cards">
<div class="card"><div class="metric">{summary['total']}</div><div class="label">档案×轮次组合</div></div>
<div class="card"><div class="metric">{generation.get('recommended',0)}</div><div class="label">生成推荐（{_percent(summary['generation_rate'])}）</div></div>
<div class="card"><div class="metric">{strict.get('passed',0)}</div><div class="label">总体严格通过（{_percent(summary['overall_strict_pass_rate'])}）</div></div>
<div class="card"><div class="metric">{strict.get('failed',0)}</div><div class="label">明确违规</div></div>
<div class="card"><div class="metric">{summary['requirements_coverage_counts'].get('not_auditable',0)}</div><div class="label">含未覆盖原始语义</div></div>
<div class="card"><div class="metric">{consistency['issue_count']}</div><div class="label">三方数据差异</div></div>
</div>
<p>推荐内硬约束通过率：<strong>{_percent(summary['recommended_hard_pass_rate'])}</strong>；推荐内真实性通过率：<strong>{_percent(summary['recommended_authenticity_pass_rate'])}</strong>。空候选 {generation.get('empty_candidate',0)} 条，规划无解 {generation.get('planning_infeasible',0)} 条。</p>
</section>
<section id="data"><h2>二、数据源与一致性</h2>
<div class="scroll"><table><thead><tr><th>数据源</th><th>菜谱</th><th>档案</th><th>食材节点</th><th>被菜谱使用的食材</th><th>菜谱营养</th><th>食材关系</th></tr></thead><tbody>
<tr><td>PostgreSQL</td><td>{counts['postgresql']['recipes']}</td><td>{counts['postgresql']['profiles']}</td><td>{counts['postgresql']['ingredients_all']}</td><td>{counts['postgresql']['ingredients_used']}</td><td>{counts['postgresql']['recipe_nutrition']}</td><td>{counts['postgresql']['recipe_ingredient_relations']}</td></tr>
<tr><td>Neo4j</td><td>{counts['neo4j']['recipes']}</td><td>不存储</td><td>{counts['neo4j']['ingredients']}</td><td>见关系</td><td>不存储</td><td>{counts['neo4j']['recipe_ingredient_relations']}</td></tr>
</tbody></table></div>
<p class="muted">1245是完整食材节点数；1239是至少被一道菜谱使用的食材数，两者不是同一口径。</p>{consistency_details}</section>
<section id="rules"><h2>三、验收规则统计</h2><div class="scroll"><table><thead><tr><th>规则</th><th>通过</th><th>失败</th><th>无法自动验收</th></tr></thead><tbody>{rule_rows}</tbody></table></div>
<h3>最终轮结构化追溯</h3><p>冻结JSON {traceability['total']}条，推荐 {traceability['checked_recommendations']} 条，逐菜检查 {traceability['checked_dishes']} 次；JSON内部追溯异常 {traceability['violation_count']} 条。与逐轮HTML的菜单差异 {traceability['cross_artifact_difference_count']} 条，仅作跨执行产物提示，不判定为真实性违规。</p>{traceability_details}{cross_artifact_details}</section>
<section id="dialogues"><h2>四、20组对话汇总</h2><div class="scroll"><table><thead><tr><th>对话</th><th>组合数</th><th>生成</th><th>空候选</th><th>规划无解</th><th>严格通过</th><th>违规</th><th>无法审计</th></tr></thead><tbody>{dialogue_rows}</tbody></table></div></section>
<section id="extraction"><h2>五、原始要求与提取覆盖</h2><p>人工预期共29个对话轮次，提取展示不完整 {extraction['failed_turn_count']} 轮；另有 {extraction['unsupported_requirement_count']} 项当前模型未覆盖，不能静默计为通过。</p><div class="scroll"><table><thead><tr><th>对话:轮次</th><th>提取状态</th><th>应提取但缺失</th><th>当前不支持</th></tr></thead><tbody>{extraction_rows}</tbody></table></div></section>
<section id="details"><h2>六、违规与无法验收明细</h2><p>共 {len(violation_cases)} 条推荐需要关注。每条规则都保留预期值、实际值和证据。</p>{violation_details}</section>
<section><h2>七、验收边界</h2><ul><li>制作时间按每道菜时间下界检查，整桌并行烹饪工期需专项测试。</li><li>正向口味、菜系、功效和人群按偏好记录，不计入第一版硬约束扣分。</li><li>空候选和规划无解属于未完成推荐，不记成硬约束违规。</li><li>冻结JSON只保存最终轮；1450条逐轮结果来自既有HTML报告。本次未重新调用模型。</li></ul></section>
</main></body></html>"""


def _render_case_detail(case: Mapping[str, Any]) -> str:
    failed_rules = [
        rule
        for rule in case["rules"]
        if rule["status"] in {"fail", "not_auditable"}
    ]
    label = (
        f"对话{case['dialogue_id']} 第{case['turn_number']}轮 "
        f"档案{case['profile_id']}｜{case['strict_status']}"
    )
    return (
        f"<details><summary>{_escape(label)}</summary>"
        f"<p>菜单：{_escape('、'.join(case['selected_recipes']) or '无')}</p>"
        f"<pre>{_escape(json.dumps(failed_rules, ensure_ascii=False, indent=2, default=_json_default))}</pre>"
        "</details>"
    )


def _render_json_details(value: Any, label: str) -> str:
    if not value:
        return "<p class='ok-text'>未发现异常。</p>"
    return (
        f"<details><summary>{_escape(label)}（{len(value)}条）</summary>"
        f"<pre>{_escape(json.dumps(value, ensure_ascii=False, indent=2, default=_json_default))}</pre>"
        "</details>"
    )


def _load_project_config() -> dict[str, Any]:
    try:
        with (REPO_ROOT / "pyproject.toml").open("rb") as stream:
            config = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise AcceptanceAuditError("无法读取pyproject.toml数据库配置") from exc
    value = config.get("tool", {}).get("mealagent")
    if not isinstance(value, dict):
        raise AcceptanceAuditError("pyproject.toml缺少tool.mealagent配置")
    return value


def _required_config(config: Mapping[str, Any], section: str, key: str) -> str:
    value = config.get(section)
    if not isinstance(value, dict):
        raise AcceptanceAuditError(f"缺少配置：tool.mealagent.{section}")
    return _required_mapping_value(value, key, section)


def _required_mapping_value(config: Mapping[str, Any], key: str, section: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AcceptanceAuditError(f"{section}缺少有效的{key}配置")
    return value


def _scalar_count(connection: Any, table_name: str) -> int:
    allowed = {
        "user_profiles",
        "recipes",
        "recipe_nutrition",
        "ingredients",
        "recipe_ingredients",
    }
    if table_name not in allowed:
        raise AcceptanceAuditError(f"不允许统计表：{table_name}")
    return int(connection.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one())


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AcceptanceAuditError(f"无法读取JSON：{path}") from exc
    except json.JSONDecodeError as exc:
        raise AcceptanceAuditError(f"JSON格式错误：{path}：{exc}") from exc


def _file_metadata(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise AcceptanceAuditError(f"无法计算文件哈希：{path}") from exc
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }


def _write_json(path: Path, value: Any) -> None:
    _write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, default=_json_default),
    )


def _write_text(path: Path, value: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    except OSError as exc:
        raise AcceptanceAuditError(f"无法写入验收产物：{path}") from exc


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    raise TypeError(f"无法序列化类型：{type(value).__name__}")


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _percent(value: float | None) -> str:
    return "无适用项" if value is None else f"{value:.2f}%"


if __name__ == "__main__":
    raise SystemExit(main())
