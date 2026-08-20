"""菜谱推荐资格双提示审计、校验与原子应用。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Iterable


EXPECTED_RECIPE_COUNT = 1912
ALLOWED_REASON_CODES = {
    "finished_item",
    "preparation_only",
    "ingredient_only",
    "fragment",
    "non_food",
}
ALLOWED_CONFIDENCES = {"high", "medium", "low"}
AUDIT_INPUT_FIELDS = ("name", "ingredients", "atomic_steps", "dish_type")
DECISION_FIELDS = {
    "recipe_name",
    "is_recommendable",
    "reason_code",
    "confidence",
    "reason",
}
REVIEW_FIELDS = (
    "recipe_name",
    "prompt_a_value",
    "prompt_a_code",
    "prompt_a_reason",
    "prompt_b_value",
    "prompt_b_code",
    "prompt_b_reason",
    "reviewer_value",
    "reviewer_note",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE_PATH = (
    REPO_ROOT / "datas" / "processed" / "Recipes" / "RecipeComplete.json"
)
DEFAULT_AUDIT_DIR = (
    REPO_ROOT / "datas" / "processed" / "Recipes" / "recommendability_audit"
)
DEFAULT_ENV_PATH = REPO_ROOT / ".env"

AuditProvider = Callable[[str, list[dict[str, Any]]], Any]


class RecommendabilityAuditError(Exception):
    """带HTTP语义状态码的推荐资格审计错误。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def _raise(status_code: int, message: str) -> None:
    raise RecommendabilityAuditError(status_code, message)


def _read_json(path: Path, *, status_code: int = 400) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _raise(status_code, f"无法读取JSON文件 {path}: {exc}")


def _write_json_atomic(path: Path, value: Any) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(path)
    except OSError as exc:
        _raise(500, f"无法写入JSON文件 {path}: {exc}")


def _load_recipes(path: Path) -> list[dict[str, Any]]:
    value = _read_json(path)
    if not isinstance(value, list) or not value:
        _raise(400, "正式菜谱必须是非空数组")

    names: set[str] = set()
    for index, recipe in enumerate(value):
        if not isinstance(recipe, dict):
            _raise(400, f"recipes[{index}]必须是对象")
        name = recipe.get("name")
        if not isinstance(name, str) or not name.strip():
            _raise(400, f"recipes[{index}].name必须是非空字符串")
        if name in names:
            _raise(400, f"菜谱名称重复：{name}")
        names.add(name)
        for field in AUDIT_INPUT_FIELDS[1:]:
            if field not in recipe:
                _raise(400, f"菜谱{name}缺少字段：{field}")
    return value


def _without_qualification(recipes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in recipe.items() if key != "is_recommendable"}
        for recipe in recipes
    ]


def _baseline_hash(recipes: Iterable[dict[str, Any]]) -> str:
    encoded = json.dumps(
        _without_qualification(recipes),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest(recipes: list[dict[str, Any]], batch_size: int) -> dict[str, Any]:
    return {
        "baseline_sha256": _baseline_hash(recipes),
        "recipe_names": [recipe["name"] for recipe in recipes],
        "batch_size": batch_size,
    }


def _validate_manifest(
    recipes: list[dict[str, Any]],
    manifest: Any,
    *,
    batch_size: int | None = None,
) -> None:
    if not isinstance(manifest, dict):
        _raise(409, "审计清单结构非法")
    if manifest.get("baseline_sha256") != _baseline_hash(recipes):
        _raise(409, "正式菜谱源基线已变化")
    if manifest.get("recipe_names") != [recipe["name"] for recipe in recipes]:
        _raise(409, "审计清单与正式菜谱名称或顺序不一致")
    if batch_size is not None and manifest.get("batch_size") != batch_size:
        _raise(409, "续跑批次大小与已有审计不一致")


def _audit_batch(recipes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {field: deepcopy(recipe[field]) for field in AUDIT_INPUT_FIELDS}
        for recipe in recipes
    ]


def _validate_decisions(
    raw: Any,
    batch: list[dict[str, Any]],
    *,
    variant: str,
) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) != len(batch):
        _raise(502, f"提示{variant.upper()}返回项数与批次不一致")

    expected_names = [recipe["name"] for recipe in batch]
    actual_names: list[Any] = []
    decisions: list[dict[str, Any]] = []
    for index, decision in enumerate(raw):
        if not isinstance(decision, dict) or set(decision) != DECISION_FIELDS:
            _raise(502, f"提示{variant.upper()} decisions[{index}]结构非法")
        actual_names.append(decision.get("recipe_name"))
        value = decision.get("is_recommendable")
        reason_code = decision.get("reason_code")
        confidence = decision.get("confidence")
        reason = decision.get("reason")
        if type(value) is not bool:
            _raise(502, f"提示{variant.upper()} {actual_names[-1]}资格必须是布尔值")
        if reason_code not in ALLOWED_REASON_CODES:
            _raise(502, f"提示{variant.upper()} {actual_names[-1]}原因代码非法")
        if confidence not in ALLOWED_CONFIDENCES:
            _raise(502, f"提示{variant.upper()} {actual_names[-1]}置信度非法")
        if not isinstance(reason, str) or not reason.strip():
            _raise(502, f"提示{variant.upper()} {actual_names[-1]}说明不能为空")
        if value != (reason_code == "finished_item"):
            _raise(502, f"提示{variant.upper()} {actual_names[-1]}资格与原因代码冲突")
        decisions.append(deepcopy(decision))

    if actual_names != expected_names:
        _raise(502, f"提示{variant.upper()}返回菜名覆盖或顺序非法")
    return decisions


def _checkpoint_path(audit_dir: Path, batch_index: int, variant: str) -> Path:
    return audit_dir / f"prompt_{variant}_batch_{batch_index:03d}.json"


def _load_or_generate_decisions(
    *,
    audit_dir: Path,
    batch_index: int,
    variant: str,
    batch: list[dict[str, Any]],
    provider: AuditProvider,
    resume: bool,
) -> tuple[list[dict[str, Any]], bool]:
    path = _checkpoint_path(audit_dir, batch_index, variant)
    if path.exists():
        if not resume:
            _raise(409, f"审计检查点已存在：{path.name}")
        return _validate_decisions(
            _read_json(path, status_code=409), batch, variant=variant
        ), False

    try:
        raw = provider(variant, _audit_batch(batch))
    except RecommendabilityAuditError:
        raise
    except Exception as exc:
        _raise(503, f"提示{variant.upper()}模型不可用：{exc}")
    decisions = _validate_decisions(raw, batch, variant=variant)
    _write_json_atomic(path, decisions)
    return decisions, True


def _build_resolutions(
    recipes: list[dict[str, Any]],
    prompt_a: list[dict[str, Any]],
    prompt_b: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    resolutions: list[dict[str, Any]] = []
    for recipe, decision_a, decision_b in zip(
        recipes, prompt_a, prompt_b, strict=True
    ):
        is_agreed = (
            decision_a["confidence"] == "high"
            and decision_b["confidence"] == "high"
            and decision_a["is_recommendable"]
            == decision_b["is_recommendable"]
            and decision_a["reason_code"] == decision_b["reason_code"]
        )
        resolutions.append(
            {
                "recipe_name": recipe["name"],
                "status": "auto_approved" if is_agreed else "manual_review",
                "is_recommendable": (
                    decision_a["is_recommendable"] if is_agreed else None
                ),
                "prompt_a": decision_a,
                "prompt_b": decision_b,
            }
        )
    return resolutions


def _write_manual_review(path: Path, resolutions: list[dict[str, Any]]) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    try:
        with temporary_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(
                stream, fieldnames=REVIEW_FIELDS, lineterminator="\n"
            )
            writer.writeheader()
            for item in resolutions:
                if item["status"] != "manual_review":
                    continue
                decision_a = item["prompt_a"]
                decision_b = item["prompt_b"]
                writer.writerow(
                    {
                        "recipe_name": item["recipe_name"],
                        "prompt_a_value": str(
                            decision_a["is_recommendable"]
                        ).lower(),
                        "prompt_a_code": decision_a["reason_code"],
                        "prompt_a_reason": decision_a["reason"],
                        "prompt_b_value": str(
                            decision_b["is_recommendable"]
                        ).lower(),
                        "prompt_b_code": decision_b["reason_code"],
                        "prompt_b_reason": decision_b["reason"],
                        "reviewer_value": "",
                        "reviewer_note": "",
                    }
                )
        temporary_path.replace(path)
    except OSError as exc:
        _raise(500, f"无法写入人工审核CSV {path}: {exc}")


def generate_audit(
    recipe_path: str | Path,
    audit_dir: str | Path,
    provider: AuditProvider,
    *,
    batch_size: int = 20,
    resume: bool = False,
) -> dict[str, int]:
    """生成双提示检查点、合并结果和人工审核表。"""
    recipe_path = Path(recipe_path)
    audit_dir = Path(audit_dir)
    if type(batch_size) is not int or not 1 <= batch_size <= 20:
        _raise(400, "batch_size必须是1到20的整数")
    if type(resume) is not bool or not callable(provider):
        _raise(400, "resume或provider非法")

    recipes = _load_recipes(recipe_path)
    manifest_path = audit_dir / "manifest.json"
    if audit_dir.exists() and any(audit_dir.iterdir()) and not resume:
        _raise(409, "审计输出目录非空，必须显式续跑")

    audit_dir.mkdir(parents=True, exist_ok=True)
    if resume:
        if not manifest_path.exists():
            _raise(409, "续跑缺少审计清单")
        _validate_manifest(
            recipes,
            _read_json(manifest_path, status_code=409),
            batch_size=batch_size,
        )
    else:
        _write_json_atomic(manifest_path, _manifest(recipes, batch_size))

    all_a: list[dict[str, Any]] = []
    all_b: list[dict[str, Any]] = []
    model_call_count = 0
    for batch_index, start in enumerate(range(0, len(recipes), batch_size)):
        batch = recipes[start : start + batch_size]
        decisions_a, called_a = _load_or_generate_decisions(
            audit_dir=audit_dir,
            batch_index=batch_index,
            variant="a",
            batch=batch,
            provider=provider,
            resume=resume,
        )
        decisions_b, called_b = _load_or_generate_decisions(
            audit_dir=audit_dir,
            batch_index=batch_index,
            variant="b",
            batch=batch,
            provider=provider,
            resume=resume,
        )
        model_call_count += int(called_a) + int(called_b)
        all_a.extend(decisions_a)
        all_b.extend(decisions_b)

    resolutions = _build_resolutions(recipes, all_a, all_b)
    _write_json_atomic(audit_dir / "resolutions.json", resolutions)
    _write_manual_review(audit_dir / "manual_review.csv", resolutions)
    auto_count = sum(item["status"] == "auto_approved" for item in resolutions)
    return {
        "recipe_count": len(recipes),
        "model_call_count": model_call_count,
        "auto_approved_count": auto_count,
        "manual_review_count": len(recipes) - auto_count,
    }


def _load_resolutions(
    recipes: list[dict[str, Any]], audit_dir: Path
) -> list[dict[str, Any]]:
    raw = _read_json(audit_dir / "resolutions.json", status_code=409)
    if not isinstance(raw, list) or len(raw) != len(recipes):
        _raise(409, "审计合并结果数量不完整")
    if [item.get("recipe_name") for item in raw if isinstance(item, dict)] != [
        recipe["name"] for recipe in recipes
    ]:
        _raise(409, "审计合并结果菜名覆盖或顺序非法")

    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            _raise(409, f"resolutions[{index}]必须是对象")
        status = item.get("status")
        value = item.get("is_recommendable")
        if status == "auto_approved":
            if type(value) is not bool:
                _raise(409, f"{item['recipe_name']}自动结果缺少布尔资格")
        elif status == "manual_review":
            if value is not None:
                _raise(409, f"{item['recipe_name']}人工项不得预填最终资格")
        else:
            _raise(409, f"{item['recipe_name']}审计状态非法")
        for variant in ("a", "b"):
            decision = _validate_decisions(
                [item.get(f"prompt_{variant}")],
                [{"name": item["recipe_name"]}],
                variant=variant,
            )[0]
            if decision["recipe_name"] != item["recipe_name"]:
                _raise(409, f"{item['recipe_name']}提示判定菜名不一致")
    return raw


def _load_review_values(
    review_path: Path, manual_names: list[str]
) -> dict[str, bool]:
    try:
        with review_path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != REVIEW_FIELDS:
                _raise(409, "人工审核CSV列结构非法")
            rows = list(reader)
    except RecommendabilityAuditError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        _raise(500, f"无法读取人工审核CSV {review_path}: {exc}")

    names = [row.get("recipe_name") for row in rows]
    if names != manual_names:
        _raise(409, "人工审核CSV缺项、重复、额外项或顺序非法")
    values: dict[str, bool] = {}
    for row in rows:
        raw_value = row.get("reviewer_value")
        if raw_value not in {"true", "false"}:
            _raise(409, f"{row.get('recipe_name')}人工审核值必须是true或false")
        values[row["recipe_name"]] = raw_value == "true"
    return values


def validate_audit(
    recipe_path: str | Path,
    audit_dir: str | Path,
    review_path: str | Path | None = None,
    *,
    expected_recipe_count: int = EXPECTED_RECIPE_COUNT,
) -> dict[str, bool]:
    """校验源基线、合并结果与人工审核，并返回最终资格映射。"""
    if type(expected_recipe_count) is not int or expected_recipe_count <= 0:
        _raise(400, "expected_recipe_count必须是正整数")
    recipe_path = Path(recipe_path)
    audit_dir = Path(audit_dir)
    review_path = (
        Path(review_path) if review_path is not None else audit_dir / "manual_review.csv"
    )
    recipes = _load_recipes(recipe_path)
    if len(recipes) != expected_recipe_count:
        _raise(
            400,
            f"正式菜谱数量必须为{expected_recipe_count}，实际为{len(recipes)}",
        )
    _validate_manifest(
        recipes, _read_json(audit_dir / "manifest.json", status_code=409)
    )
    resolutions = _load_resolutions(recipes, audit_dir)
    manual_names = [
        item["recipe_name"]
        for item in resolutions
        if item["status"] == "manual_review"
    ]
    reviewed_values = _load_review_values(review_path, manual_names)
    return {
        item["recipe_name"]: (
            item["is_recommendable"]
            if item["status"] == "auto_approved"
            else reviewed_values[item["recipe_name"]]
        )
        for item in resolutions
    }


def apply_audit(
    recipe_path: str | Path,
    audit_dir: str | Path,
    review_path: str | Path | None = None,
    *,
    expected_recipe_count: int = EXPECTED_RECIPE_COUNT,
) -> dict[str, bool]:
    """通过全部校验后只写回is_recommendable字段。"""
    recipe_path = Path(recipe_path)
    mapping = validate_audit(
        recipe_path,
        audit_dir,
        review_path,
        expected_recipe_count=expected_recipe_count,
    )
    recipes = _load_recipes(recipe_path)
    applied = deepcopy(recipes)
    for recipe in applied:
        recipe["is_recommendable"] = mapping[recipe["name"]]
    _write_json_atomic(recipe_path, applied)
    return mapping


def create_llm_audit_provider(
    env_path: str | Path = DEFAULT_ENV_PATH,
) -> AuditProvider:
    """按项目环境创建严格结构化的双提示审计Provider。"""
    _load_environment_file(Path(env_path))
    try:
        from backend.infrastructure.llm import create_chat_model_from_environment

        chat = create_chat_model_from_environment()
        structured_chat = chat.with_structured_output(
            _build_llm_output_schema(),
            method="function_calling",
        )
    except Exception as exc:
        _raise(500, f"无法创建推荐资格审计模型：{exc}")

    def provider(variant: str, batch: list[dict[str, Any]]) -> Any:
        result = structured_chat.invoke(_build_audit_prompt(variant, batch))
        if not isinstance(result, dict):
            _raise(502, "推荐资格模型结构化输出必须是对象")
        return result.get("decisions")

    return provider


def _build_llm_output_schema() -> dict[str, Any]:
    decision_schema = {
        "type": "object",
        "properties": {
            "recipe_name": {"type": "string"},
            "is_recommendable": {"type": "boolean"},
            "reason_code": {
                "type": "string",
                "enum": sorted(ALLOWED_REASON_CODES),
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
            },
            "reason": {"type": "string"},
        },
        "required": sorted(DECISION_FIELDS),
        "additionalProperties": False,
    }
    return {
        "title": "RecipeRecommendabilityAuditBatch",
        "description": "按输入顺序返回每道菜的推荐资格审计结果",
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "items": decision_schema,
            }
        },
        "required": ["decisions"],
        "additionalProperties": False,
    }


def _build_audit_prompt(
    variant: str,
    batch: list[dict[str, Any]],
) -> str:
    shared_rules = """
你正在审计菜谱是否可以作为一桌菜单中的独立成品条目。
必须逐项、按输入顺序返回，不能缺项、重复、改名或增加菜名。
判断必须同时依据菜名、食材和步骤，不能因为dish_type就直接判定。
reason_code只有：
- finished_item：完整成品，is_recommendable=true；
- preparation_only：仅清洗、切配、浸泡、焯水、解冻等准备操作；
- ingredient_only：只是食材或原料；
- fragment：步骤或流程片段，不能独立成为菜单条目；
- non_food：非食物内容。
除finished_item外都必须is_recommendable=false。证据不充分时降低confidence，
不得为了减少人工审核而给high。reason使用简短中文并指出直接依据。
""".strip()
    if variant == "a":
        focus = (
            "提示A：从正向完整性审查。重点确认名称、食材和步骤是否共同"
            "构成可以端上桌的完整菜、汤、主食、小菜或甜品。"
        )
    elif variant == "b":
        focus = (
            "提示B：从反向排除审查。先主动寻找准备操作、单一食材、流程"
            "片段或非食物证据；只有排除这些情况后才判为finished_item。"
        )
    else:
        _raise(400, f"未知提示版本：{variant}")
    payload = json.dumps(batch, ensure_ascii=False, separators=(",", ":"))
    return f"{shared_rules}\n\n{focus}\n\n待审计菜谱JSON：\n{payload}"


def _load_environment_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        _raise(500, f"无法读取环境配置文件 {path}: {exc}")
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name:
            os.environ.setdefault(name, value)


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="菜谱推荐资格审计")
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("generate", "validate", "apply"):
        subparser = subparsers.add_parser(action)
        subparser.add_argument("--recipe-path", type=Path, default=DEFAULT_RECIPE_PATH)
        subparser.add_argument("--audit-dir", type=Path, default=DEFAULT_AUDIT_DIR)
        subparser.add_argument(
            "--expected-recipe-count",
            type=int,
            default=EXPECTED_RECIPE_COUNT,
        )
        if action == "generate":
            subparser.add_argument("--batch-size", type=int, default=20)
            subparser.add_argument("--resume", action="store_true")
            subparser.add_argument("--env-path", type=Path, default=DEFAULT_ENV_PATH)
        else:
            subparser.add_argument("--review-path", type=Path)
    return parser


def main() -> None:
    args = _build_argument_parser().parse_args()
    try:
        if args.action == "generate":
            result: Any = generate_audit(
                args.recipe_path,
                args.audit_dir,
                create_llm_audit_provider(args.env_path),
                batch_size=args.batch_size,
                resume=args.resume,
            )
        elif args.action == "validate":
            mapping = validate_audit(
                args.recipe_path,
                args.audit_dir,
                args.review_path,
                expected_recipe_count=args.expected_recipe_count,
            )
            result = {"validated_recipe_count": len(mapping)}
        else:
            mapping = apply_audit(
                args.recipe_path,
                args.audit_dir,
                args.review_path,
                expected_recipe_count=args.expected_recipe_count,
            )
            result = {"applied_recipe_count": len(mapping)}
    except RecommendabilityAuditError as exc:
        raise SystemExit(f"status_code={exc.status_code}, message={exc}") from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
