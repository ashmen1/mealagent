from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import tomllib
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from sqlalchemy import text

from backend.core.dish_filtering_contract import GROUP_TAGS
from backend.infrastructure.database import create_database_engine
from backend.infrastructure.llm.langchain_constraints import (
    _read_required_environment_variable,
    build_lowest_reasoning_config,
)


REPOSITORY_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
RECIPE_PATH: Final[Path] = (
    REPOSITORY_ROOT / "datas" / "processed" / "Recipes" / "RecipeComplete.json"
)
REVIEW_PATH: Final[Path] = (
    REPOSITORY_ROOT
    / "datas"
    / "processed"
    / "Recipes"
    / "RecipeLabelCompletionReview.csv"
)
PYPROJECT_PATH: Final[Path] = REPOSITORY_ROOT / "pyproject.toml"
ENV_PATH: Final[Path] = REPOSITORY_ROOT / ".env"

EXPECTED_RECIPE_COUNT: Final[int] = 1912
EXPECTED_EMPTY_LABEL_COUNT: Final[int] = 206
SIMILAR_RECIPE_LIMIT: Final[int] = 8
DEFAULT_WORKERS: Final[int] = 8
DEFAULT_BATCH_SIZE: Final[int] = 20

GROUP_FIELD_NAMES: Final[dict[str, str]] = {
    "餐次": "meal",
    "口味": "taste",
    "菜系": "cuisine",
    "功效": "effect",
    "人群": "population",
}
PRIMARY_REVIEW_GROUPS: Final[tuple[str, ...]] = ("餐次", "口味")
SENSITIVE_REVIEW_GROUPS: Final[tuple[str, ...]] = ("功效", "人群")
STANDARD_TAGS: Final[tuple[str, ...]] = tuple(
    tag for group_tags in GROUP_TAGS.values() for tag in group_tags
)
STANDARD_TAG_SET: Final[frozenset[str]] = frozenset(STANDARD_TAGS)
TAG_ORDER: Final[dict[str, int]] = {
    tag: index for index, tag in enumerate(STANDARD_TAGS)
}
TAG_NORMALIZATION: Final[dict[str, str]] = {
    "微辣": "辣",
    "香辣": "辣",
    "麻辣": "辣",
    "咸鲜": "咸",
    "养胃": "养胃健胃消食",
    "健胃消食": "养胃健胃消食",
    "便秘": "养胃健胃消食",
}
CONFIDENCE_ORDER: Final[dict[str, int]] = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
}
REVIEWABLE_STATUSES: Final[frozenset[str]] = frozenset(
    {"auto_approved", "approved"}
)
AMBIGUOUS_NAME_MARKERS: Final[tuple[str, ...]] = (
    "测试菜",
    "绞肉",
    "套餐",
    "合辑",
    "同烹",
    "一锅蒸",
    "二人食",
    "&",
    "|",
)
EXPLICIT_NAME_CUES: Final[dict[str, tuple[str, ...]]] = {
    "甜": (
        "甜",
        "红糖",
        "冰糖",
        "蜂蜜",
        "蜜汁",
        "豆沙",
        "双皮奶",
        "提拉米苏",
        "冰皮月饼",
    ),
    "辣": ("辣", "剁椒", "泡椒", "小米辣", "红咖喱", "黄椒酱"),
    "咸": ("咸", "盐焗", "盐烤", "盐酥", "椒盐", "腊"),
    "酸": ("酸", "醋", "柠檬", "梅酱"),
    "清淡": ("清蒸", "白灼", "清炖", "低盐"),
    "西餐风味": (
        "意式",
        "法式",
        "地中海",
        "惠灵顿",
        "提拉米苏",
        "塔可",
        "意大利",
    ),
    "东北菜": ("东北", "锅包"),
    "粤菜": ("粤式", "广式", "啫啫", "煲仔"),
    "川湘菜": ("川味", "湘味", "剁椒", "辣子", "泡椒"),
    "江浙菜": ("江浙", "苏式", "杭式", "本帮", "上海"),
}
HEALTH_GUIDANCE_URLS: Final[str] = (
    "https://dg.cnsoc.org/|"
    "https://www.nhc.gov.cn/xcs/c100122/202206/"
    "d4941efa5d6544e2abac68127c3238c0.shtml"
)

CSV_FIELD_NAMES: Final[tuple[str, ...]] = (
    "recipe_name",
    "dish_type",
    "meal_tags",
    "meal_confidence",
    "meal_evidence",
    "taste_tags",
    "taste_confidence",
    "taste_evidence",
    "cuisine_tags",
    "cuisine_confidence",
    "cuisine_evidence",
    "effect_tags",
    "effect_confidence",
    "effect_evidence",
    "population_tags",
    "population_confidence",
    "population_evidence",
    "similar_recipes",
    "nutrition_density_per_100g",
    "needs_web_research",
    "source_urls",
    "health_guidance_urls",
    "review_required_groups",
    "review_status",
    "review_notes",
    "baseline_nonempty_labels_sha256",
)

LabelClassifier = Callable[[str], dict[str, Any]]


class LabelCompletionError(Exception):
    """菜谱空标签补全流程错误。"""


def load_recipes(path: Path = RECIPE_PATH) -> list[dict[str, Any]]:
    """读取正式菜谱并校验顶层结构。"""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise LabelCompletionError("RecipeComplete.json 顶层必须是数组")
    if len(value) != EXPECTED_RECIPE_COUNT:
        raise LabelCompletionError(
            f"正式菜谱数量必须为 {EXPECTED_RECIPE_COUNT}，实际为 {len(value)}"
        )
    return value


def write_recipes(
    recipes: list[dict[str, Any]],
    path: Path = RECIPE_PATH,
) -> None:
    """以临时文件原子替换正式菜谱，避免写入中断留下半个 JSON。"""
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(recipes, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def select_empty_label_recipes(
    recipes: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """只选择标签为合法空数组的正式菜谱。"""
    selected: list[dict[str, Any]] = []
    for recipe in recipes:
        labels = recipe.get("labels")
        if not isinstance(labels, list):
            raise LabelCompletionError(f"{recipe.get('name')} 的 labels 必须是数组")
        if not labels:
            selected.append(recipe)
    return selected


def normalize_tags(tags: object, group_name: str) -> list[str]:
    """归一化 LLM 候选，并按标准标签顺序去重。"""
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise LabelCompletionError(f"{group_name}候选必须是字符串数组")
    allowed_tags = frozenset(GROUP_TAGS[group_name])
    normalized: list[str] = []
    for tag in tags:
        canonical_tag = TAG_NORMALIZATION.get(tag.strip(), tag.strip())
        if canonical_tag not in allowed_tags:
            raise LabelCompletionError(
                f"{group_name}候选包含未知标签：{canonical_tag!r}"
            )
        if canonical_tag not in normalized:
            normalized.append(canonical_tag)
    return sorted(normalized, key=TAG_ORDER.__getitem__)


def combine_group_tags(group_tags: dict[str, list[str]]) -> list[str]:
    """按五个标准分组的固定顺序合并标签。"""
    combined: list[str] = []
    for group_name in GROUP_TAGS:
        for tag in group_tags[group_name]:
            if tag not in combined:
                combined.append(tag)
    return combined


def calculate_nonempty_labels_hash(recipes: Iterable[dict[str, Any]]) -> str:
    """计算原有非空标签的稳定哈希，阻止审核期间被意外覆盖。"""
    snapshot = [
        [recipe.get("name"), recipe.get("labels")]
        for recipe in recipes
        if recipe.get("labels")
    ]
    encoded = json.dumps(
        snapshot,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def find_similar_recipes(
    target: dict[str, Any],
    labeled_recipes: Iterable[dict[str, Any]],
    limit: int = SIMILAR_RECIPE_LIMIT,
) -> list[dict[str, Any]]:
    """按食材、菜名二元组和菜品类型计算内部相似样本。"""
    target_ingredients = set(target.get("ingredients", {}))
    target_name_tokens = _name_bigrams(str(target.get("name", "")))
    scored: list[dict[str, Any]] = []
    for recipe in labeled_recipes:
        ingredient_score = _jaccard(
            target_ingredients,
            set(recipe.get("ingredients", {})),
        )
        name_score = _jaccard(
            target_name_tokens,
            _name_bigrams(str(recipe.get("name", ""))),
        )
        dish_type_score = float(
            target.get("dish_type") == recipe.get("dish_type")
        )
        score = (
            ingredient_score * Decimal("0.65")
            + name_score * Decimal("0.20")
            + Decimal(str(dish_type_score)) * Decimal("0.15")
        )
        if score <= 0:
            continue
        labels = [
            tag
            for tag in recipe.get("labels", [])
            if tag in STANDARD_TAG_SET
        ]
        scored.append(
            {
                "name": recipe.get("name"),
                "score": float(score),
                "labels": sorted(set(labels), key=TAG_ORDER.__getitem__),
            }
        )
    return sorted(
        scored,
        key=lambda item: (-item["score"], str(item["name"])),
    )[:limit]


def calculate_tag_support(
    similar_recipes: Iterable[dict[str, Any]],
) -> dict[str, tuple[float, int]]:
    """返回每个标准标签的相似度加权支持率和支持样本数。"""
    neighbors = list(similar_recipes)
    total_score = sum(float(item["score"]) for item in neighbors)
    support: dict[str, tuple[float, int]] = {}
    for tag in STANDARD_TAGS:
        matching = [item for item in neighbors if tag in item["labels"]]
        weighted_score = sum(float(item["score"]) for item in matching)
        support[tag] = (
            weighted_score / total_score if total_score else 0.0,
            len(matching),
        )
    return support


def classify_group_confidence(
    recipe_name: str,
    selected_tags: list[str],
    support: dict[str, tuple[float, int]],
) -> str:
    """按明确名称线索和内部样本支持计算组级最低置信度。"""
    if not selected_tags:
        return "none"
    tag_confidences: list[str] = []
    for tag in selected_tags:
        support_rate, support_count = support[tag]
        has_explicit_cue = any(
            cue in recipe_name for cue in EXPLICIT_NAME_CUES.get(tag, ())
        )
        if has_explicit_cue or (support_count >= 3 and support_rate >= 0.70):
            tag_confidences.append("high")
        elif support_rate >= 0.40:
            tag_confidences.append("medium")
        else:
            tag_confidences.append("low")
    return min(tag_confidences, key=CONFIDENCE_ORDER.__getitem__)


def build_candidate_row(
    recipe: dict[str, Any],
    labeled_recipes: list[dict[str, Any]],
    nutrition_density: dict[str, float],
    classifier: LabelClassifier,
    baseline_hash: str,
) -> dict[str, str]:
    """为一道空标签菜生成可审核候选行。"""
    similar_recipes = find_similar_recipes(recipe, labeled_recipes)
    support = calculate_tag_support(similar_recipes)
    raw_result = classifier(
        _build_classifier_prompt(recipe, similar_recipes, nutrition_density)
    )
    if not isinstance(raw_result, dict):
        raise LabelCompletionError(f"{recipe.get('name')} 的 LLM 结果必须是对象")

    row = _initialize_candidate_row(
        recipe,
        similar_recipes,
        nutrition_density,
        baseline_hash,
    )
    selected_by_group, confidence_by_group = _populate_group_candidates(
        row,
        raw_result,
        support,
    )
    review_required_groups, has_ambiguous_name = _assess_review_requirements(
        row["recipe_name"],
        selected_by_group,
        confidence_by_group,
    )
    if has_ambiguous_name:
        review_required_groups.append("菜品身份")
    row["needs_web_research"] = str(
        has_ambiguous_name
        or (
            bool(selected_by_group["菜系"])
            and confidence_by_group["菜系"] == "low"
        )
    ).lower()
    row["review_required_groups"] = "|".join(
        dict.fromkeys(review_required_groups)
    )
    row["review_status"] = (
        "pending" if review_required_groups else "auto_approved"
    )
    return row


def _initialize_candidate_row(
    recipe: dict[str, Any],
    similar_recipes: list[dict[str, Any]],
    nutrition_density: dict[str, float],
    baseline_hash: str,
) -> dict[str, str]:
    """创建候选审核行，并填充不依赖 LLM 分组输出的字段。"""
    row = {field_name: "" for field_name in CSV_FIELD_NAMES}
    row["recipe_name"] = str(recipe.get("name", ""))
    row["dish_type"] = str(recipe.get("dish_type", ""))
    row["similar_recipes"] = "|".join(
        f"{item['name']}({item['score']:.3f})" for item in similar_recipes
    )
    row["nutrition_density_per_100g"] = json.dumps(
        nutrition_density,
        ensure_ascii=False,
        sort_keys=True,
    )
    row["health_guidance_urls"] = HEALTH_GUIDANCE_URLS
    row["baseline_nonempty_labels_sha256"] = baseline_hash
    return row


def _populate_group_candidates(
    row: dict[str, str],
    raw_result: dict[str, Any],
    support: dict[str, tuple[float, int]],
) -> tuple[dict[str, list[str]], dict[str, str]]:
    """校验并写入五个标签组的候选、置信度和依据。"""
    selected_by_group: dict[str, list[str]] = {}
    confidence_by_group: dict[str, str] = {}
    for group_name, field_prefix in GROUP_FIELD_NAMES.items():
        selected_tags = normalize_tags(
            raw_result.get(f"{field_prefix}_tags"),
            group_name,
        )
        evidence = raw_result.get(f"{field_prefix}_evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            raise LabelCompletionError(
                f"{row['recipe_name']} 的 {group_name}依据必须是非空字符串"
            )
        confidence = classify_group_confidence(
            row["recipe_name"],
            selected_tags,
            support,
        )
        support_text = "；".join(
            f"{tag}={support[tag][0]:.0%}/{support[tag][1]}例"
            for tag in selected_tags
        )
        row[f"{field_prefix}_tags"] = _format_tag_cell(selected_tags)
        row[f"{field_prefix}_confidence"] = confidence
        row[f"{field_prefix}_evidence"] = evidence.strip() + (
            f"；内部支持：{support_text}" if support_text else ""
        )
        selected_by_group[group_name] = selected_tags
        confidence_by_group[group_name] = confidence
    return selected_by_group, confidence_by_group


def _assess_review_requirements(
    recipe_name: str,
    selected_by_group: dict[str, list[str]],
    confidence_by_group: dict[str, str],
) -> tuple[list[str], bool]:
    """根据组级置信度、敏感标签和菜名歧义确定人工审核项。"""
    review_groups = [
        group_name
        for group_name in PRIMARY_REVIEW_GROUPS
        if not selected_by_group[group_name]
        or confidence_by_group[group_name] != "high"
    ]
    if selected_by_group["菜系"] and confidence_by_group["菜系"] != "high":
        review_groups.append("菜系")
    review_groups.extend(
        group_name
        for group_name in SENSITIVE_REVIEW_GROUPS
        if selected_by_group[group_name]
    )
    has_ambiguous_name = any(
        marker in recipe_name for marker in AMBIGUOUS_NAME_MARKERS
    )
    return review_groups, has_ambiguous_name


def write_review_rows(
    rows: Iterable[dict[str, str]],
    path: Path = REVIEW_PATH,
) -> None:
    """以 Excel 可直接打开的 UTF-8 BOM CSV 写入审核结果。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=list(CSV_FIELD_NAMES),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def load_review_rows(path: Path = REVIEW_PATH) -> list[dict[str, str]]:
    """读取审核 CSV，并拒绝表头漂移。"""
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != CSV_FIELD_NAMES:
            raise LabelCompletionError("审核 CSV 表头不符合预期")
        return [dict(row) for row in reader]


def validate_review_rows(
    recipes: list[dict[str, Any]],
    review_rows: list[dict[str, str]],
) -> dict[str, list[str]]:
    """校验审核覆盖、标签范围、状态及正式数据基线。"""
    empty_recipes = select_empty_label_recipes(recipes)
    empty_names = [str(recipe.get("name", "")) for recipe in empty_recipes]
    review_names = [row.get("recipe_name", "") for row in review_rows]
    if len(review_names) != len(set(review_names)):
        raise LabelCompletionError("审核 CSV 存在重复菜名")
    if set(review_names) != set(empty_names):
        missing = sorted(set(empty_names) - set(review_names))
        extra = sorted(set(review_names) - set(empty_names))
        raise LabelCompletionError(
            f"审核 CSV 未精确覆盖当前空标签菜：missing={missing}, extra={extra}"
        )

    expected_hash = calculate_nonempty_labels_hash(recipes)
    hashes = {
        row.get("baseline_nonempty_labels_sha256", "")
        for row in review_rows
    }
    if hashes != {expected_hash}:
        raise LabelCompletionError("原有非空标签在审核期间发生变化")

    validated: dict[str, list[str]] = {}
    for row in review_rows:
        recipe_name = row["recipe_name"]
        status = row.get("review_status", "").strip()
        if status not in REVIEWABLE_STATUSES:
            raise LabelCompletionError(
                f"{recipe_name} 的 review_status 尚未通过：{status!r}"
            )
        if status == "auto_approved" and row.get(
            "review_required_groups", ""
        ).strip():
            raise LabelCompletionError(
                f"{recipe_name} 含风险项，不能使用 auto_approved"
            )

        group_tags: dict[str, list[str]] = {}
        for group_name, field_prefix in GROUP_FIELD_NAMES.items():
            tags = _parse_review_tag_cell(
                row.get(f"{field_prefix}_tags", ""),
                group_name,
                recipe_name,
            )
            group_tags[group_name] = tags
        combined = combine_group_tags(group_tags)
        if not combined:
            raise LabelCompletionError(f"{recipe_name} 审核后仍没有标准标签")
        validated[recipe_name] = combined
    return validated


def apply_review_rows(
    recipes: list[dict[str, Any]],
    review_rows: list[dict[str, str]],
) -> dict[str, int]:
    """将已通过审核的标签应用到内存数据，且只允许修改空标签菜。"""
    validated = validate_review_rows(recipes, review_rows)
    updated = 0
    for recipe in recipes:
        recipe_name = str(recipe.get("name", ""))
        if recipe_name not in validated:
            continue
        if recipe.get("labels") != []:
            raise LabelCompletionError(f"{recipe_name} 已不再是空标签菜")
        recipe["labels"] = validated[recipe_name]
        updated += 1
    return {
        "updated_recipes": updated,
        "label_occurrences": sum(len(recipe["labels"]) for recipe in recipes),
        "remaining_empty_labels": len(select_empty_label_recipes(recipes)),
    }


def generate_review_file(
    recipes: list[dict[str, Any]],
    nutrition_density_by_name: dict[str, dict[str, float]],
    classifier: LabelClassifier,
    review_path: Path,
    *,
    workers: int,
    batch_size: int,
    is_resume: bool,
) -> dict[str, int]:
    """并发生成审核 CSV；批次失败时保留已完成结果供续跑。"""
    empty_recipes = select_empty_label_recipes(recipes)
    if len(empty_recipes) != EXPECTED_EMPTY_LABEL_COUNT:
        raise LabelCompletionError(
            f"generate 仅适用于 {EXPECTED_EMPTY_LABEL_COUNT} 道空标签基线，"
            f"当前为 {len(empty_recipes)}"
        )
    labeled_recipes = [recipe for recipe in recipes if recipe.get("labels")]
    baseline_hash = calculate_nonempty_labels_hash(recipes)
    recipe_order = {
        str(recipe.get("name", "")): index
        for index, recipe in enumerate(empty_recipes)
    }
    completed_rows = _load_completed_review_rows(
        review_path,
        is_resume=is_resume,
        recipe_order=recipe_order,
        baseline_hash=baseline_hash,
    )

    pending = [
        recipe
        for recipe in empty_recipes
        if str(recipe.get("name", "")) not in completed_rows
    ]
    for batch_start in range(0, len(pending), batch_size):
        batch = pending[batch_start : batch_start + batch_size]
        generated_rows, failures = _generate_candidate_batch(
            batch,
            labeled_recipes,
            nutrition_density_by_name,
            classifier,
            baseline_hash,
            workers,
        )
        completed_rows.update(generated_rows)

        ordered_rows = sorted(
            completed_rows.values(),
            key=lambda row: recipe_order[row["recipe_name"]],
        )
        write_review_rows(ordered_rows, review_path)
        print(
            f"候选生成进度：{len(completed_rows)}/{len(empty_recipes)}",
            flush=True,
        )
        if failures:
            details = "; ".join(
                f"{name}: {message}" for name, message in failures
            )
            raise LabelCompletionError(
                f"本批次存在 {len(failures)} 个失败项，已保留成功结果：{details}"
            )

    return {
        "review_rows": len(completed_rows),
        "pending_review": sum(
            row["review_status"] == "pending"
            for row in completed_rows.values()
        ),
        "auto_approved": sum(
            row["review_status"] == "auto_approved"
            for row in completed_rows.values()
        ),
    }


def _load_completed_review_rows(
    review_path: Path,
    *,
    is_resume: bool,
    recipe_order: dict[str, int],
    baseline_hash: str,
) -> dict[str, dict[str, str]]:
    """读取并校验可续跑的候选审核检查点。"""
    if not review_path.exists():
        return {}
    if not is_resume:
        raise LabelCompletionError(
            f"审核文件已存在：{review_path}；如需续跑请使用 --resume"
        )

    completed_rows: dict[str, dict[str, str]] = {}
    for row in load_review_rows(review_path):
        recipe_name = row.get("recipe_name", "")
        if recipe_name not in recipe_order:
            raise LabelCompletionError(f"续跑文件包含未知菜名：{recipe_name}")
        if row.get("baseline_nonempty_labels_sha256") != baseline_hash:
            raise LabelCompletionError("续跑文件与当前非空标签基线不一致")
        completed_rows[recipe_name] = row
    return completed_rows


def _generate_candidate_batch(
    recipes: list[dict[str, Any]],
    labeled_recipes: list[dict[str, Any]],
    nutrition_density_by_name: dict[str, dict[str, float]],
    classifier: LabelClassifier,
    baseline_hash: str,
    workers: int,
) -> tuple[dict[str, dict[str, str]], list[tuple[str, str]]]:
    """并发生成一个批次，并将成功结果与失败详情分开返回。"""
    generated_rows: dict[str, dict[str, str]] = {}
    failures: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_recipe = {
            executor.submit(
                build_candidate_row,
                recipe,
                labeled_recipes,
                nutrition_density_by_name[str(recipe.get("name", ""))],
                classifier,
                baseline_hash,
            ): recipe
            for recipe in recipes
        }
        for future in as_completed(future_to_recipe):
            recipe_name = str(future_to_recipe[future].get("name", ""))
            try:
                generated_rows[recipe_name] = future.result()
            except Exception as exc:
                failures.append((recipe_name, str(exc)))
    return generated_rows, failures


def load_nutrition_density_by_name(
    recipes: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    """读取空标签菜的九项营养并换算为每100克配方证据。"""
    with PYPROJECT_PATH.open("rb") as stream:
        project_config = tomllib.load(stream)
    try:
        database_url = project_config["tool"]["mealagent"]["database"]["url"]
    except (KeyError, TypeError) as exc:
        raise LabelCompletionError("pyproject.toml 缺少正式数据库URL") from exc

    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT r.name,
                           n.energy_kcal,
                           n.protein_g,
                           n.fat_g,
                           n.carbohydrate_g,
                           n.fiber_g,
                           n.sodium_mg,
                           n.calcium_mg,
                           n.iron_mg,
                           n.cholesterol_mg
                    FROM recipes AS r
                    JOIN recipe_nutrition AS n ON n.recipe_id = r.id
                    """
                )
            ).mappings().all()
    except Exception as exc:
        raise LabelCompletionError(f"读取 PostgreSQL 营养结果失败：{exc}") from exc
    finally:
        engine.dispose()

    nutrition_by_name = {str(row["name"]): row for row in rows}
    result: dict[str, dict[str, float]] = {}
    for recipe in select_empty_label_recipes(recipes):
        recipe_name = str(recipe.get("name", ""))
        if recipe_name not in nutrition_by_name:
            raise LabelCompletionError(f"缺少 {recipe_name} 的营养计算结果")
        resolutions = recipe.get("ingredient_quantity_resolutions")
        if not isinstance(resolutions, dict):
            raise LabelCompletionError(f"{recipe_name} 缺少最终食材克重证据")
        included_weight = sum(
            Decimal(str(item["resolved_quantity_g"]))
            for item in resolutions.values()
            if not item.get("is_nutrition_excluded")
        )
        if included_weight <= 0:
            raise LabelCompletionError(f"{recipe_name} 的有效配方克重必须大于0")
        scale = Decimal("100") / included_weight
        row = nutrition_by_name[recipe_name]
        result[recipe_name] = {
            field_name: round(float(Decimal(str(row[field_name])) * scale), 2)
            for field_name in (
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
        }
    return result


def create_anthropic_classifier() -> LabelClassifier:
    """使用项目现有 Anthropic 配置创建严格结构化标签分类器。"""
    _load_environment_file(ENV_PATH)
    provider = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()
    if provider != "anthropic":
        raise LabelCompletionError("空标签补全当前只支持项目已有 Anthropic Provider")

    from langchain_anthropic import ChatAnthropic

    chat = ChatAnthropic(
        model=_read_required_environment_variable("ANTHROPIC_MODEL"),
        base_url=_read_required_environment_variable("ANTHROPIC_BASE_URL"),
        api_key=_read_required_environment_variable("ANTHROPIC_AUTH_TOKEN"),
        temperature=0,
        timeout=90,
        max_retries=0,
        **build_lowest_reasoning_config(),
    )
    structured_chat = chat.with_structured_output(
        _build_output_schema(),
        method="function_calling",
    )

    def classify(prompt: str) -> dict[str, Any]:
        result = structured_chat.invoke(prompt)
        if not isinstance(result, dict):
            raise LabelCompletionError("Anthropic 结构化输出不是对象")
        return result

    return classify


def _build_output_schema() -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for group_name, field_prefix in GROUP_FIELD_NAMES.items():
        tag_field = f"{field_prefix}_tags"
        evidence_field = f"{field_prefix}_evidence"
        properties[tag_field] = {
            "type": "array",
            "items": {"type": "string", "enum": list(GROUP_TAGS[group_name])},
            "uniqueItems": True,
            "description": f"{group_name}候选；证据不足时为空数组",
        }
        properties[evidence_field] = {
            "type": "string",
            "description": f"{group_name}候选或留空的直接依据",
        }
        required.extend((tag_field, evidence_field))
    return {
        "title": "RecipeLabelCandidates",
        "description": "一道菜的五组标准标签候选与依据",
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _build_classifier_prompt(
    recipe: dict[str, Any],
    similar_recipes: list[dict[str, Any]],
    nutrition_density: dict[str, float],
) -> str:
    ingredients = "、".join(
        f"{name}:{quantity}"
        for name, quantity in recipe.get("ingredients", {}).items()
    )
    steps = "；".join(
        str(item.get("text", ""))
        for item in recipe.get("atomic_steps", [])
        if isinstance(item, dict)
    )[:4000]
    examples = "\n".join(
        f"- {item['name']}，相似度{item['score']:.3f}，标准标签："
        f"{','.join(item['labels']) or '无'}"
        for item in similar_recipes
    )
    return f"""你是菜谱标签审核助手。只允许从下面五组标准标签中选择，不得创造新标签：
餐次：{','.join(GROUP_TAGS['餐次'])}
口味：{','.join(GROUP_TAGS['口味'])}
菜系：{','.join(GROUP_TAGS['菜系'])}
功效：{','.join(GROUP_TAGS['功效'])}
人群：{','.join(GROUP_TAGS['人群'])}

标注原则：
1. 餐次和口味尽量给出有直接依据的候选，可多选。
2. 菜系只有来源或烹饪风格明确时才选，不得把所有中餐强行归入菜系。
3. 功效和人群保守判断，证据不足必须返回空数组。
4. 营养数据只作证据，不能仅凭单项营养宣称助眠、养胃、贫血或适合哺乳。
5. 相似菜标签是内部参考，不得机械复制。
6. 每组都必须给出候选或留空的简短中文依据。

菜名：{recipe.get('name')}
菜品类型：{recipe.get('dish_type')}
时间下界：{recipe.get('total_time_lower_bound_minutes')}分钟
食材：{ingredients}
步骤：{steps}
每100克配方营养证据：{json.dumps(nutrition_density, ensure_ascii=False)}
内部相似菜：
{examples or '- 无'}
"""


def _load_environment_file(path: Path) -> None:
    """读取项目.env中的必需配置，不覆盖已设置的进程环境变量。"""
    if not path.exists():
        raise LabelCompletionError(f"缺少环境配置文件：{path}")
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise LabelCompletionError(f".env第{line_number}行格式错误")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _parse_review_tag_cell(
    value: str,
    group_name: str,
    recipe_name: str,
) -> list[str]:
    tags = [tag.strip() for tag in value.split("|") if tag.strip()]
    if len(tags) != len(set(tags)):
        raise LabelCompletionError(f"{recipe_name} 的 {group_name}标签重复")
    allowed = frozenset(GROUP_TAGS[group_name])
    unknown = sorted(set(tags) - allowed)
    if unknown:
        raise LabelCompletionError(
            f"{recipe_name} 的 {group_name}包含未知标签：{unknown}"
        )
    expected_order = sorted(tags, key=TAG_ORDER.__getitem__)
    if tags != expected_order:
        raise LabelCompletionError(
            f"{recipe_name} 的 {group_name}标签未按标准顺序填写"
        )
    return tags


def _format_tag_cell(tags: Iterable[str]) -> str:
    return "|".join(tags)


def _name_bigrams(name: str) -> set[str]:
    normalized = re.sub(r"[^\w]", "", name, flags=re.UNICODE)
    if len(normalized) < 2:
        return {normalized} if normalized else set()
    return {
        normalized[index : index + 2]
        for index in range(len(normalized) - 1)
    }


def _jaccard(left: set[str], right: set[str]) -> Decimal:
    union = left | right
    if not union:
        return Decimal("0")
    return Decimal(len(left & right)) / Decimal(len(union))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="为正式菜谱中的空标签生成、校验并应用人工审核候选。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser("generate", help="生成审核CSV")
    generate_parser.add_argument(
        "--review-file",
        type=Path,
        default=REVIEW_PATH,
    )
    generate_parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
    )
    generate_parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
    )
    generate_parser.add_argument("--resume", action="store_true")

    command_help = {
        "validate": "校验人工审核结果",
        "apply": "应用已通过审核的标签",
    }
    for command, help_text in command_help.items():
        command_parser = subparsers.add_parser(command, help=help_text)
        command_parser.add_argument(
            "--review-file",
            type=Path,
            default=REVIEW_PATH,
        )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    recipes = load_recipes()
    if args.command == "generate":
        result = _run_generate_command(args, recipes)
    else:
        result = _run_review_command(args, recipes)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _run_generate_command(
    args: argparse.Namespace,
    recipes: list[dict[str, Any]],
) -> dict[str, int]:
    """执行候选生成命令。"""
    if args.workers <= 0 or args.batch_size <= 0:
        raise LabelCompletionError("workers和batch-size必须为正整数")
    return generate_review_file(
        recipes,
        load_nutrition_density_by_name(recipes),
        create_anthropic_classifier(),
        args.review_file,
        workers=args.workers,
        batch_size=args.batch_size,
        is_resume=args.resume,
    )


def _run_review_command(
    args: argparse.Namespace,
    recipes: list[dict[str, Any]],
) -> dict[str, int]:
    """执行审核校验或正式应用命令。"""
    review_rows = load_review_rows(args.review_file)
    if args.command == "validate":
        return {"validated_recipes": len(validate_review_rows(recipes, review_rows))}

    result = apply_review_rows(recipes, review_rows)
    write_recipes(recipes)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
