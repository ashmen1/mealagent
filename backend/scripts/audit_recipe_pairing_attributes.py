"""菜谱荤素、冷热与主烹饪方式的双提示审计。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Iterable

from backend.core.recipe_pairing import (
    COMPOSITION_TYPES,
    PAIRING_ATTRIBUTE_FIELDS,
    PRIMARY_COOKING_METHODS,
    SERVING_TEMPERATURES,
)


EXPECTED_RECIPE_COUNT = 1912
ALLOWED_CONFIDENCES = ("high", "medium", "low")
DECISION_FIELDS = {
    "recipe_name",
    "serving_temperature",
    "primary_cooking_method",
    "confidence",
    "reason",
}
MODEL_DECISION_FIELDS = {
    "recipe_name",
    "temperature",
    "method",
    "confidence",
    "reason",
}
AUDIT_INPUT_FIELDS = (
    "name",
    "ingredients",
    "atomic_steps",
    "dish_type",
    "labels",
)
REVIEW_FIELDS = (
    "recipe_name",
    "composition_type",
    "prompt_a_temperature",
    "prompt_a_method",
    "prompt_a_confidence",
    "prompt_a_reason",
    "prompt_b_temperature",
    "prompt_b_method",
    "prompt_b_confidence",
    "prompt_b_reason",
    "reviewer_serving_temperature",
    "reviewer_primary_cooking_method",
    "reviewer_note",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE_PATH = (
    REPO_ROOT / "datas" / "processed" / "Recipes" / "RecipeComplete.json"
)
DEFAULT_INGREDIENT_PATH = (
    REPO_ROOT
    / "datas"
    / "processed"
    / "Ingredients"
    / "Ingredients2Nutrition.csv"
)
DEFAULT_AUDIT_DIR = (
    REPO_ROOT / "datas" / "processed" / "Recipes" / "pairing_audit"
)
DEFAULT_ENV_PATH = REPO_ROOT / ".env"

AuditProvider = Callable[[str, list[dict[str, Any]]], Any]


class RecipePairingAuditError(Exception):
    """菜谱搭配属性审计的可预期错误。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def derive_composition_type(
    ingredients: dict[str, Any],
    categories: dict[str, str],
) -> str:
    """严格按标准分类判定荤素，不读取菜名或步骤。"""

    if not isinstance(ingredients, dict) or not ingredients:
        _raise(400, "菜谱ingredients必须是非空对象")
    for ingredient_name in ingredients:
        category = categories.get(ingredient_name)
        if category is None:
            _raise(400, f"食材不存在于标准分类：{ingredient_name}")
        if category in {"肉类", "水产"}:
            return "荤"
    return "素"


def generate_audit(
    recipe_path: str | Path,
    ingredient_path: str | Path,
    audit_dir: str | Path,
    provider: AuditProvider,
    *,
    batch_size: int = 20,
    resume: bool = False,
    expected_recipe_count: int = EXPECTED_RECIPE_COUNT,
) -> dict[str, int]:
    """生成双提示检查点、合并结论和人工审核表。"""

    if type(batch_size) is not int or not 1 <= batch_size <= 20:
        _raise(400, "batch_size必须是1到20的整数")
    if type(resume) is not bool or not callable(provider):
        _raise(400, "resume或provider非法")
    _validate_expected_count(expected_recipe_count)

    recipe_path = Path(recipe_path)
    ingredient_path = Path(ingredient_path)
    audit_dir = Path(audit_dir)
    recipes = _load_recipes(recipe_path, expected_recipe_count)
    categories = _load_categories(ingredient_path)
    compositions = {
        recipe["name"]: derive_composition_type(
            recipe["ingredients"], categories
        )
        for recipe in recipes
    }
    manifest_path = audit_dir / "manifest.json"
    if audit_dir.exists() and any(audit_dir.iterdir()) and not resume:
        _raise(409, "审计输出目录非空，必须显式续跑")
    audit_dir.mkdir(parents=True, exist_ok=True)
    manifest = _build_manifest(recipes, categories, batch_size)
    if resume:
        if not manifest_path.exists():
            _raise(409, "续跑缺少审计清单")
        _require_manifest(
            _read_json(manifest_path, 409),
            manifest,
            require_batch_size=True,
        )
    else:
        _write_json_atomic(manifest_path, manifest)

    prompt_a: list[dict[str, Any]] = []
    prompt_b: list[dict[str, Any]] = []
    model_call_count = 0
    for batch_index, start in enumerate(range(0, len(recipes), batch_size)):
        batch = recipes[start : start + batch_size]
        decisions_a, called_a = _load_or_generate(
            audit_dir,
            batch_index,
            "a",
            batch,
            provider,
            resume,
        )
        decisions_b, called_b = _load_or_generate(
            audit_dir,
            batch_index,
            "b",
            batch,
            provider,
            resume,
        )
        prompt_a.extend(decisions_a)
        prompt_b.extend(decisions_b)
        model_call_count += int(called_a) + int(called_b)

    resolutions = _build_resolutions(
        recipes,
        compositions,
        prompt_a,
        prompt_b,
    )
    _write_json_atomic(audit_dir / "resolutions.json", resolutions)
    _write_manual_review(audit_dir / "manual_review.csv", resolutions)
    auto_count = sum(item["status"] == "auto_approved" for item in resolutions)
    return {
        "recipe_count": len(recipes),
        "model_call_count": model_call_count,
        "auto_approved_count": auto_count,
        "manual_review_count": len(recipes) - auto_count,
    }


def validate_audit(
    recipe_path: str | Path,
    ingredient_path: str | Path,
    audit_dir: str | Path,
    review_path: str | Path | None = None,
    *,
    expected_recipe_count: int = EXPECTED_RECIPE_COUNT,
) -> dict[str, dict[str, str]]:
    """校验源基线、双提示结论与人工审核，返回最终属性。"""

    _validate_expected_count(expected_recipe_count)
    recipe_path = Path(recipe_path)
    ingredient_path = Path(ingredient_path)
    audit_dir = Path(audit_dir)
    review_path = (
        Path(review_path)
        if review_path is not None
        else audit_dir / "manual_review.csv"
    )
    recipes = _load_recipes(recipe_path, expected_recipe_count)
    categories = _load_categories(ingredient_path)
    current_manifest = _build_manifest(recipes, categories, None)
    _require_manifest(
        _read_json(audit_dir / "manifest.json", 409),
        current_manifest,
        require_batch_size=False,
    )
    resolutions = _load_resolutions(recipes, categories, audit_dir)
    manual_names = [
        item["recipe_name"]
        for item in resolutions
        if item["status"] == "manual_review"
    ]
    reviewed = _load_review_values(review_path, manual_names)
    return _build_final_attributes(resolutions, reviewed)


def _build_final_attributes(
    resolutions: list[dict[str, Any]],
    reviewed: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    """合并自动结论与人工结论，生成最终搭配属性。"""

    result: dict[str, dict[str, str]] = {}
    for item in resolutions:
        if item["status"] == "auto_approved":
            temperature = item["serving_temperature"]
            method = item["primary_cooking_method"]
        else:
            temperature = reviewed[item["recipe_name"]][
                "serving_temperature"
            ]
            method = reviewed[item["recipe_name"]][
                "primary_cooking_method"
            ]
        result[item["recipe_name"]] = {
            "composition_type": item["composition_type"],
            "serving_temperature": temperature,
            "primary_cooking_method": method,
        }
    return result


def apply_audit(
    recipe_path: str | Path,
    ingredient_path: str | Path,
    audit_dir: str | Path,
    review_path: str | Path | None = None,
    *,
    expected_recipe_count: int = EXPECTED_RECIPE_COUNT,
) -> dict[str, dict[str, str]]:
    """全部校验通过后，原子写回且只写三项搭配属性。"""

    recipe_path = Path(recipe_path)
    mapping = validate_audit(
        recipe_path,
        ingredient_path,
        audit_dir,
        review_path,
        expected_recipe_count=expected_recipe_count,
    )
    recipes = _load_recipes(recipe_path, expected_recipe_count)
    applied = deepcopy(recipes)
    for recipe in applied:
        recipe.update(mapping[recipe["name"]])
    _write_json_atomic(recipe_path, applied)
    return mapping


def _load_recipes(
    path: Path,
    expected_recipe_count: int,
) -> list[dict[str, Any]]:
    raw = _read_json(path, 400)
    if not isinstance(raw, list) or not raw:
        _raise(400, "正式菜谱必须是非空数组")
    if len(raw) != expected_recipe_count:
        _raise(
            400,
            f"正式菜谱数量必须为{expected_recipe_count}，实际为{len(raw)}",
        )
    result: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            _raise(400, f"recipes[{index}]必须是对象")
        name = value.get("name")
        if not isinstance(name, str) or not name.strip():
            _raise(400, f"recipes[{index}].name必须是非空字符串")
        if name in names:
            _raise(400, f"菜谱名称重复：{name}")
        names.add(name)
        for field in AUDIT_INPUT_FIELDS[1:]:
            if field not in value:
                _raise(400, f"菜谱{name}缺少字段：{field}")
        if not isinstance(value["ingredients"], dict) or not value["ingredients"]:
            _raise(400, f"菜谱{name}.ingredients必须是非空对象")
        result.append(value)
    return result


def _load_categories(path: Path) -> dict[str, str]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not {"标准食材名", "分类"}.issubset(reader.fieldnames or ()):
                _raise(400, "标准食材分类CSV缺少标准食材名或分类列")
            rows = list(reader)
    except RecipePairingAuditError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        _raise(400, f"无法读取标准食材分类CSV：{exc}")
    result: dict[str, str] = {}
    for line_number, row in enumerate(rows, start=2):
        name = row.get("标准食材名")
        category = row.get("分类")
        if not isinstance(name, str) or not name.strip():
            _raise(400, f"标准食材分类第{line_number}行食材名为空")
        if name in result:
            _raise(400, f"标准食材名称重复：{name}")
        if not isinstance(category, str) or not category.strip():
            _raise(400, f"食材{name}分类为空")
        result[name] = category
    return result


def _load_or_generate(
    audit_dir: Path,
    batch_index: int,
    variant: str,
    batch: list[dict[str, Any]],
    provider: AuditProvider,
    resume: bool,
) -> tuple[list[dict[str, Any]], bool]:
    path = audit_dir / f"prompt_{variant}_batch_{batch_index:03d}.json"
    if path.exists():
        if not resume:
            _raise(409, f"审计检查点已存在：{path.name}")
        return _validate_decisions(
            _read_json(path, 409), batch, variant
        ), False
    payload = [
        {field: deepcopy(recipe[field]) for field in AUDIT_INPUT_FIELDS}
        for recipe in batch
    ]
    try:
        raw = provider(variant, payload)
    except RecipePairingAuditError:
        raise
    except Exception as exc:
        _raise(503, f"提示{variant.upper()}模型不可用：{exc}")
    decisions = _validate_decisions(raw, batch, variant)
    _write_json_atomic(path, decisions)
    return decisions, True


def _validate_decisions(
    raw: Any,
    batch: list[dict[str, Any]],
    variant: str,
) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) != len(batch):
        _raise(502, f"提示{variant.upper()}返回项数与批次不一致")
    expected_names = [recipe["name"] for recipe in batch]
    result: list[dict[str, Any]] = []
    actual_names: list[Any] = []
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            _raise(
                502,
                f"提示{variant.upper()} decisions[{index}]结构非法："
                f"期望对象，实际{type(value).__name__}",
            )
        actual_fields = set(value)
        if actual_fields != DECISION_FIELDS:
            missing_fields = sorted(DECISION_FIELDS - actual_fields)
            extra_fields = sorted(actual_fields - DECISION_FIELDS)
            _raise(
                502,
                f"提示{variant.upper()} decisions[{index}]结构非法："
                f"recipe_name={value.get('recipe_name')!r}，"
                f"缺少字段={missing_fields}，额外字段={extra_fields}，"
                f"实际字段={sorted(actual_fields)}",
            )
        name = value.get("recipe_name")
        actual_names.append(name)
        if value.get("serving_temperature") not in SERVING_TEMPERATURES:
            _raise(502, f"提示{variant.upper()} {name} serving_temperature非法")
        if value.get("primary_cooking_method") not in PRIMARY_COOKING_METHODS:
            _raise(502, f"提示{variant.upper()} {name} primary_cooking_method非法")
        if value.get("confidence") not in ALLOWED_CONFIDENCES:
            _raise(502, f"提示{variant.upper()} {name} confidence非法")
        reason = value.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            _raise(502, f"提示{variant.upper()} {name} reason不能为空")
        result.append(deepcopy(value))
    if actual_names != expected_names:
        _raise(502, f"提示{variant.upper()}返回菜名覆盖或顺序非法")
    return result


def _build_resolutions(
    recipes: list[dict[str, Any]],
    compositions: dict[str, str],
    prompt_a: list[dict[str, Any]],
    prompt_b: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result = []
    for recipe, decision_a, decision_b in zip(
        recipes, prompt_a, prompt_b, strict=True
    ):
        is_agreed = (
            decision_a["confidence"] == "high"
            and decision_b["confidence"] == "high"
            and decision_a["serving_temperature"]
            == decision_b["serving_temperature"]
            and decision_a["primary_cooking_method"]
            == decision_b["primary_cooking_method"]
        )
        result.append(
            {
                "recipe_name": recipe["name"],
                "status": "auto_approved" if is_agreed else "manual_review",
                "composition_type": compositions[recipe["name"]],
                "serving_temperature": (
                    decision_a["serving_temperature"] if is_agreed else None
                ),
                "primary_cooking_method": (
                    decision_a["primary_cooking_method"] if is_agreed else None
                ),
                "prompt_a": decision_a,
                "prompt_b": decision_b,
            }
        )
    return result


def _write_manual_review(
    path: Path,
    resolutions: list[dict[str, Any]],
) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=REVIEW_FIELDS,
                lineterminator="\n",
            )
            writer.writeheader()
            for item in resolutions:
                if item["status"] != "manual_review":
                    continue
                writer.writerow(_build_manual_review_row(item))
        temporary.replace(path)
    except OSError as exc:
        _raise(500, f"无法写入人工审核CSV：{exc}")


def _build_manual_review_row(item: dict[str, Any]) -> dict[str, str]:
    a = item["prompt_a"]
    b = item["prompt_b"]
    return {
        "recipe_name": item["recipe_name"],
        "composition_type": item["composition_type"],
        "prompt_a_temperature": a["serving_temperature"],
        "prompt_a_method": a["primary_cooking_method"],
        "prompt_a_confidence": a["confidence"],
        "prompt_a_reason": a["reason"],
        "prompt_b_temperature": b["serving_temperature"],
        "prompt_b_method": b["primary_cooking_method"],
        "prompt_b_confidence": b["confidence"],
        "prompt_b_reason": b["reason"],
        "reviewer_serving_temperature": "",
        "reviewer_primary_cooking_method": "",
        "reviewer_note": "",
    }


def _load_resolutions(
    recipes: list[dict[str, Any]],
    categories: dict[str, str],
    audit_dir: Path,
) -> list[dict[str, Any]]:
    raw = _read_json(audit_dir / "resolutions.json", 409)
    if not isinstance(raw, list) or len(raw) != len(recipes):
        _raise(409, "审计合并结果数量不完整")
    expected_names = [recipe["name"] for recipe in recipes]
    actual_names = [item.get("recipe_name") for item in raw if isinstance(item, dict)]
    if actual_names != expected_names:
        _raise(409, "审计合并结果菜名覆盖或顺序非法")
    for recipe, item in zip(recipes, raw, strict=True):
        if not isinstance(item, dict):
            _raise(409, "审计合并结果存在非对象项")
        composition = derive_composition_type(recipe["ingredients"], categories)
        if item.get("composition_type") != composition:
            _raise(409, f"{recipe['name']}荤素结论与标准分类不一致")
        status = item.get("status")
        if status == "auto_approved":
            if item.get("serving_temperature") not in SERVING_TEMPERATURES:
                _raise(409, f"{recipe['name']}自动冷热结论非法")
            if item.get("primary_cooking_method") not in PRIMARY_COOKING_METHODS:
                _raise(409, f"{recipe['name']}自动主做法结论非法")
        elif status == "manual_review":
            if item.get("serving_temperature") is not None or item.get(
                "primary_cooking_method"
            ) is not None:
                _raise(409, f"{recipe['name']}人工项不得预填最终结论")
        else:
            _raise(409, f"{recipe['name']}审计状态非法")
        for variant in ("a", "b"):
            decision = _validate_decisions(
                [item.get(f"prompt_{variant}")],
                [recipe],
                variant,
            )[0]
            if decision["recipe_name"] != recipe["name"]:
                _raise(409, f"{recipe['name']}提示判定菜名不一致")
    return raw


def _load_review_values(
    path: Path,
    manual_names: list[str],
) -> dict[str, dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != REVIEW_FIELDS:
                _raise(409, "人工审核CSV列结构非法")
            rows = list(reader)
    except RecipePairingAuditError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        _raise(500, f"无法读取人工审核CSV：{exc}")
    names = [row.get("recipe_name") for row in rows]
    if names != manual_names:
        _raise(409, "人工审核CSV缺项、重复、额外项或顺序非法")
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        name = row["recipe_name"]
        temperature = row.get("reviewer_serving_temperature")
        method = row.get("reviewer_primary_cooking_method")
        if temperature not in SERVING_TEMPERATURES:
            _raise(409, f"{name}人工审核 serving_temperature 未完成或非法")
        if method not in PRIMARY_COOKING_METHODS:
            _raise(409, f"{name}人工审核 primary_cooking_method 未完成或非法")
        result[name] = {
            "serving_temperature": temperature,
            "primary_cooking_method": method,
        }
    return result


def _build_manifest(
    recipes: list[dict[str, Any]],
    categories: dict[str, str],
    batch_size: int | None,
) -> dict[str, Any]:
    result = {
        "recipe_baseline_sha256": _hash_json(
            _without_pairing_fields(recipes)
        ),
        "ingredient_categories_sha256": _hash_json(categories),
        "recipe_names": [recipe["name"] for recipe in recipes],
        "audit_contract_sha256": _hash_json(
            {
                "output_schema": _build_output_schema(),
                "prompt_a": _build_prompt("a", []),
                "prompt_b": _build_prompt("b", []),
            }
        ),
    }
    if batch_size is not None:
        result["batch_size"] = batch_size
    return result


def _without_pairing_fields(
    recipes: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            key: value
            for key, value in recipe.items()
            if key not in PAIRING_ATTRIBUTE_FIELDS
        }
        for recipe in recipes
    ]


def _require_manifest(
    actual: Any,
    expected: dict[str, Any],
    *,
    require_batch_size: bool,
) -> None:
    if not isinstance(actual, dict):
        _raise(409, "审计清单结构非法")
    fields = set(expected)
    if not require_batch_size:
        fields.discard("batch_size")
    for field in fields:
        if actual.get(field) != expected[field]:
            _raise(409, f"审计清单与当前源数据不一致：{field}")


def _hash_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_json(path: Path, status_code: int) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _raise(status_code, f"无法读取JSON文件 {path}：{exc}")


def _write_json_atomic(path: Path, value: Any) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError as exc:
        _raise(500, f"无法写入JSON文件 {path}：{exc}")


def create_deepseek_audit_provider(
    env_path: str | Path = DEFAULT_ENV_PATH,
) -> AuditProvider:
    """从备用环境配置创建唯一的 DeepSeek 双提示 Provider。"""

    _load_environment_file(Path(env_path))
    try:
        from langchain_openai import ChatOpenAI

        base_url = _required_environment("LLM_BASE_URL_BACKUP")
        token = _required_environment("LLM_AUTH_TOKEN_BACKUP")
        model = _required_environment("LLM_MODEL_BACKUP")
        if model != "deepseek-v4-flash":
            _raise(500, "LLM_MODEL_BACKUP必须配置为deepseek-v4-flash")
        chat = ChatOpenAI(
            model=model,
            base_url=base_url,
            api_key=token,
            temperature=0,
            timeout=60,
            max_retries=0,
            extra_body={"thinking": {"type": "disabled"}},
        )
        structured = chat.with_structured_output(
            _build_output_schema(),
            method="function_calling",
        )
    except RecipePairingAuditError:
        raise
    except Exception as exc:
        _raise(500, f"无法创建deepseek-v4-flash审计模型：{exc}")

    def provider(variant: str, batch: list[dict[str, Any]]) -> Any:
        result = structured.invoke(_build_prompt(variant, batch))
        if not isinstance(result, dict) or set(result) != {"decisions"}:
            _raise(502, "搭配属性模型结构化输出必须是对象")
        return _normalize_model_decisions(result["decisions"], variant)

    return provider


def _build_output_schema() -> dict[str, Any]:
    decision = {
        "type": "object",
        "properties": {
            "recipe_name": {"type": "string"},
            "temperature": {
                "type": "string",
                "enum": list(SERVING_TEMPERATURES),
            },
            "method": {
                "type": "string",
                "enum": list(PRIMARY_COOKING_METHODS),
            },
            "confidence": {
                "type": "string",
                "enum": list(ALLOWED_CONFIDENCES),
            },
            "reason": {"type": "string"},
        },
        "required": sorted(MODEL_DECISION_FIELDS),
        "additionalProperties": False,
    }
    return {
        "title": "RecipePairingAttributeAuditBatch",
        "type": "object",
        "properties": {
            "decisions": {"type": "array", "items": decision}
        },
        "required": ["decisions"],
        "additionalProperties": False,
    }


def _normalize_model_decisions(raw: Any, variant: str) -> list[dict[str, Any]]:
    """严格校验模型接口字段，再显式映射为业务审计字段。"""

    if not isinstance(raw, list):
        _raise(502, f"提示{variant.upper()}模型decisions必须是数组")
    normalized: list[dict[str, Any]] = []
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            _raise(
                502,
                f"提示{variant.upper()}模型decisions[{index}]必须是对象",
            )
        actual_fields = set(value)
        if actual_fields != MODEL_DECISION_FIELDS:
            _raise(
                502,
                f"提示{variant.upper()}模型decisions[{index}]结构非法："
                f"recipe_name={value.get('recipe_name')!r}，"
                f"缺少字段={sorted(MODEL_DECISION_FIELDS - actual_fields)}，"
                f"额外字段={sorted(actual_fields - MODEL_DECISION_FIELDS)}",
            )
        normalized.append(
            {
                "recipe_name": value["recipe_name"],
                "serving_temperature": value["temperature"],
                "primary_cooking_method": value["method"],
                "confidence": value["confidence"],
                "reason": value["reason"],
            }
        )
    return normalized


def _build_prompt(variant: str, batch: list[dict[str, Any]]) -> str:
    shared = """
你要为每道菜判定典型食用温度和唯一主烹饪方式。必须按输入顺序逐项返回，
不得缺项、重复、增加或修改菜名。temperature只能是热或冷，按成品通常端上
桌时的温度判断。method只能是蒸、煮、炒、炖、煎、炸、烤、拌、烧焖、冷制、
其他。含多个步骤时，选择最决定成品形态、风味和
熟化结果的核心步骤；焯水、切配、装盘等辅助步骤不能覆盖核心步骤。烧和焖
统一标为烧焖；无热加工且以混合调味成菜标为拌；无热加工且不以拌制为核心
标为冷制。证据不足时必须降低confidence，不能为了自动通过而给high。
reason用简短中文说明菜名或关键步骤证据。

每个结果对象只能包含以下五个字段，字段名必须逐字一致：recipe_name、
temperature、method、confidence、reason。不得输出近似拼写、别名、重复字段
或任何额外字段。
""".strip()
    if variant == "a":
        focus = "提示A：从成品形态、典型上桌方式和正向核心工艺进行判断。"
    elif variant == "b":
        focus = "提示B：先排除辅助步骤和次要工艺，再反向核验典型温度与核心工艺。"
    else:
        _raise(400, f"未知提示版本：{variant}")
    payload = json.dumps(batch, ensure_ascii=False, separators=(",", ":"))
    return f"{shared}\n\n{focus}\n\n待审计菜谱JSON：\n{payload}"


def _load_environment_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        _raise(500, f"无法读取环境配置：{exc}")
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(
            name.strip(), value.strip().strip('"').strip("'")
        )


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        _raise(500, f"缺少环境变量{name}")
    return value.strip()


def _validate_expected_count(value: int) -> None:
    if type(value) is not int or value <= 0:
        _raise(400, "expected_recipe_count必须是正整数")


def _raise(status_code: int, message: str) -> None:
    raise RecipePairingAuditError(status_code, message)


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="菜谱搭配属性双提示审计")
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("generate", "validate", "apply"):
        subparser = subparsers.add_parser(action)
        subparser.add_argument("--recipe-path", type=Path, default=DEFAULT_RECIPE_PATH)
        subparser.add_argument(
            "--ingredient-path", type=Path, default=DEFAULT_INGREDIENT_PATH
        )
        subparser.add_argument("--audit-dir", type=Path, default=DEFAULT_AUDIT_DIR)
        subparser.add_argument(
            "--expected-recipe-count", type=int, default=EXPECTED_RECIPE_COUNT
        )
        if action == "generate":
            subparser.add_argument("--batch-size", type=int, default=20)
            subparser.add_argument("--resume", action="store_true")
            subparser.add_argument("--env-path", type=Path, default=DEFAULT_ENV_PATH)
        else:
            subparser.add_argument("--review-path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_argument_parser().parse_args(argv)
    if args.action == "generate":
        result = generate_audit(
            args.recipe_path,
            args.ingredient_path,
            args.audit_dir,
            create_deepseek_audit_provider(args.env_path),
            batch_size=args.batch_size,
            resume=args.resume,
            expected_recipe_count=args.expected_recipe_count,
        )
    elif args.action == "validate":
        result = validate_audit(
            args.recipe_path,
            args.ingredient_path,
            args.audit_dir,
            args.review_path,
            expected_recipe_count=args.expected_recipe_count,
        )
    else:
        result = apply_audit(
            args.recipe_path,
            args.ingredient_path,
            args.audit_dir,
            args.review_path,
            expected_recipe_count=args.expected_recipe_count,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "RecipePairingAuditError",
    "apply_audit",
    "create_deepseek_audit_provider",
    "derive_composition_type",
    "generate_audit",
    "validate_audit",
]
