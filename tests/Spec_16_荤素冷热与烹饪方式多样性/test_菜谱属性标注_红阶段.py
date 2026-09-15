from __future__ import annotations

import csv
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


TEST_DIR = Path(__file__).resolve().parent
REPO_ROOT = TEST_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


FINAL_FIELDS = {
    "composition_type",
    "serving_temperature",
    "primary_cooking_method",
}


def _load_audit_module():
    try:
        return importlib.import_module(
            "backend.scripts.audit_recipe_pairing_attributes"
        )
    except ModuleNotFoundError as exc:
        pytest.fail(
            "缺少 Spec_16 菜谱搭配属性审计模块："
            "backend.scripts.audit_recipe_pairing_attributes；"
            f"输入=正式菜谱、食材分类、双提示结果；原始错误={exc}",
            pytrace=False,
        )


def _recipe(
    name: str,
    ingredient_name: str,
    *,
    dish_type: str = "菜",
) -> dict[str, Any]:
    return {
        "name": name,
        "ingredients": {ingredient_name: "100g"},
        "atomic_steps": [{"text": "制作完成后装盘"}],
        "dish_type": dish_type,
        "labels": ["晚餐"],
        "is_recommendable": True,
        "other": {"kept": True},
    }


def _write_recipes(
    path: Path,
    recipes: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    values = recipes or [
        _recipe("鸡肉蒸菜", "鸡肉"),
        _recipe("鸡蛋冷盘", "鸡蛋", dish_type="小菜"),
    ]
    path.write_text(
        json.dumps(values, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return values


def _write_ingredients(path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["标准食材名", "分类"])
        writer.writeheader()
        writer.writerows(
            [
                {"标准食材名": "鸡肉", "分类": "肉类"},
                {"标准食材名": "鲈鱼", "分类": "水产"},
                {"标准食材名": "鸡蛋", "分类": "蛋奶"},
                {"标准食材名": "豆腐", "分类": "豆类"},
            ]
        )


def _decision(
    recipe: dict[str, Any],
    *,
    temperature: str = "热",
    method: str = "蒸",
    confidence: str = "high",
) -> dict[str, Any]:
    return {
        "recipe_name": recipe["name"],
        "serving_temperature": temperature,
        "primary_cooking_method": method,
        "confidence": confidence,
        "reason": "根据典型食用温度和主要成品步骤判断",
    }


def _provider(variant: str, batch: list[dict[str, Any]]):
    del variant
    return [
        _decision(
            recipe,
            temperature=("冷" if "冷盘" in recipe["name"] else "热"),
            method=("拌" if "冷盘" in recipe["name"] else "蒸"),
        )
        for recipe in batch
    ]


def _prepare_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    recipe_path = tmp_path / "recipes.json"
    ingredient_path = tmp_path / "ingredients.csv"
    audit_dir = tmp_path / "pairing_audit"
    _write_recipes(recipe_path)
    _write_ingredients(ingredient_path)
    return recipe_path, ingredient_path, audit_dir


def _generate(
    module: Any,
    recipe_path: Path,
    ingredient_path: Path,
    audit_dir: Path,
    provider: Any = _provider,
) -> dict[str, Any]:
    return module.generate_audit(
        recipe_path,
        ingredient_path,
        audit_dir,
        provider,
        batch_size=20,
        expected_recipe_count=2,
    )


def test_generate正常路径生成全量属性和审核结论(tmp_path: Path) -> None:
    module = _load_audit_module()
    recipe_path, ingredient_path, audit_dir = _prepare_paths(tmp_path)

    summary = _generate(module, recipe_path, ingredient_path, audit_dir)

    assert summary == {
        "recipe_count": 2,
        "model_call_count": 2,
        "auto_approved_count": 2,
        "manual_review_count": 0,
    }
    resolutions = json.loads(
        (audit_dir / "resolutions.json").read_text(encoding="utf-8")
    )
    assert resolutions[0]["composition_type"] == "荤"
    assert resolutions[1]["composition_type"] == "素"
    assert resolutions[0]["serving_temperature"] == "热"
    assert resolutions[1]["serving_temperature"] == "冷"


def test_validate正常路径返回逐菜完整属性(tmp_path: Path) -> None:
    module = _load_audit_module()
    recipe_path, ingredient_path, audit_dir = _prepare_paths(tmp_path)
    _generate(module, recipe_path, ingredient_path, audit_dir)

    result = module.validate_audit(
        recipe_path,
        ingredient_path,
        audit_dir,
        audit_dir / "manual_review.csv",
        expected_recipe_count=2,
    )

    assert result == {
        "鸡肉蒸菜": {
            "composition_type": "荤",
            "serving_temperature": "热",
            "primary_cooking_method": "蒸",
        },
        "鸡蛋冷盘": {
            "composition_type": "素",
            "serving_temperature": "冷",
            "primary_cooking_method": "拌",
        },
    }


def test_apply正常路径原子写回且重复应用幂等(tmp_path: Path) -> None:
    module = _load_audit_module()
    recipe_path, ingredient_path, audit_dir = _prepare_paths(tmp_path)
    original = json.loads(recipe_path.read_text(encoding="utf-8"))
    _generate(module, recipe_path, ingredient_path, audit_dir)

    module.apply_audit(
        recipe_path,
        ingredient_path,
        audit_dir,
        audit_dir / "manual_review.csv",
        expected_recipe_count=2,
    )
    applied = json.loads(recipe_path.read_text(encoding="utf-8"))
    first_bytes = recipe_path.read_bytes()
    module.apply_audit(
        recipe_path,
        ingredient_path,
        audit_dir,
        audit_dir / "manual_review.csv",
        expected_recipe_count=2,
    )

    assert [item["composition_type"] for item in applied] == ["荤", "素"]
    assert [item["serving_temperature"] for item in applied] == ["热", "冷"]
    assert [item["primary_cooking_method"] for item in applied] == ["蒸", "拌"]
    assert [
        {key: value for key, value in item.items() if key not in FINAL_FIELDS}
        for item in applied
    ] == original
    assert recipe_path.read_bytes() == first_bytes


@pytest.mark.parametrize(
    ("ingredient_name", "category", "expected"),
    [
        ("鸡肉", "肉类", "荤"),
        ("鲈鱼", "水产", "荤"),
        ("鸡蛋", "蛋奶", "素"),
        ("豆腐", "豆类", "素"),
    ],
)
def test_荤素严格依据肉类水产且蛋奶仍为素(
    ingredient_name: str,
    category: str,
    expected: str,
) -> None:
    module = _load_audit_module()

    actual = module.derive_composition_type(
        {ingredient_name: "100g"},
        {ingredient_name: category},
    )

    assert actual == expected


def test_双提示分歧或低置信均进入人工审核(tmp_path: Path) -> None:
    module = _load_audit_module()
    recipe_path, ingredient_path, audit_dir = _prepare_paths(tmp_path)

    def provider(variant: str, batch: list[dict[str, Any]]):
        decisions = _provider(variant, batch)
        if variant == "b":
            decisions[0]["serving_temperature"] = "冷"
        if variant == "a":
            decisions[1]["confidence"] = "medium"
        return decisions

    _generate(module, recipe_path, ingredient_path, audit_dir, provider)
    resolutions = json.loads(
        (audit_dir / "resolutions.json").read_text(encoding="utf-8")
    )
    with (audit_dir / "manual_review.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        review_rows = list(csv.DictReader(stream))

    assert [item["status"] for item in resolutions] == [
        "manual_review",
        "manual_review",
    ]
    assert [row["recipe_name"] for row in review_rows] == [
        "鸡肉蒸菜",
        "鸡蛋冷盘",
    ]


def test_空菜谱数组被拒绝且不产生审核结果(tmp_path: Path) -> None:
    module = _load_audit_module()
    recipe_path = tmp_path / "recipes.json"
    ingredient_path = tmp_path / "ingredients.csv"
    audit_dir = tmp_path / "audit"
    recipe_path.write_text("[]", encoding="utf-8")
    _write_ingredients(ingredient_path)

    with pytest.raises(Exception) as captured:
        module.generate_audit(
            recipe_path,
            ingredient_path,
            audit_dir,
            _provider,
            expected_recipe_count=1912,
        )

    assert getattr(captured.value, "status_code", None) == 400
    assert not (audit_dir / "resolutions.json").exists()


def test_正式数据数量必须精确为1912道(tmp_path: Path) -> None:
    module = _load_audit_module()
    recipe_path, ingredient_path, audit_dir = _prepare_paths(tmp_path)

    with pytest.raises(Exception) as captured:
        module.generate_audit(
            recipe_path,
            ingredient_path,
            audit_dir,
            _provider,
            expected_recipe_count=1912,
        )

    assert getattr(captured.value, "status_code", None) == 400
    assert "1912" in str(captured.value)


def test_重复菜名被拒绝且不调用模型(tmp_path: Path) -> None:
    module = _load_audit_module()
    recipe_path = tmp_path / "recipes.json"
    ingredient_path = tmp_path / "ingredients.csv"
    audit_dir = tmp_path / "audit"
    repeated = _recipe("重复菜", "鸡肉")
    _write_recipes(recipe_path, [repeated, dict(repeated)])
    _write_ingredients(ingredient_path)
    calls: list[str] = []

    def provider(variant: str, batch: list[dict[str, Any]]):
        calls.append(variant)
        return _provider(variant, batch)

    with pytest.raises(Exception) as captured:
        module.generate_audit(
            recipe_path,
            ingredient_path,
            audit_dir,
            provider,
            expected_recipe_count=2,
        )

    assert getattr(captured.value, "status_code", None) == 400
    assert calls == []


def test_食材不存在于标准分类时明确拒绝(tmp_path: Path) -> None:
    module = _load_audit_module()
    recipe_path = tmp_path / "recipes.json"
    ingredient_path = tmp_path / "ingredients.csv"
    _write_recipes(recipe_path, [_recipe("未知食材菜", "不存在的食材")])
    _write_ingredients(ingredient_path)

    with pytest.raises(Exception) as captured:
        module.generate_audit(
            recipe_path,
            ingredient_path,
            tmp_path / "audit",
            _provider,
            expected_recipe_count=1,
        )

    assert getattr(captured.value, "status_code", None) == 400
    assert "不存在的食材" in str(captured.value)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("serving_temperature", "常温"),
        ("primary_cooking_method", "微波"),
        ("confidence", "unknown"),
    ],
)
def test_模型输出非法枚举返回502(
    tmp_path: Path,
    field: str,
    invalid_value: str,
) -> None:
    module = _load_audit_module()
    recipe_path, ingredient_path, audit_dir = _prepare_paths(tmp_path)

    def provider(variant: str, batch: list[dict[str, Any]]):
        decisions = _provider(variant, batch)
        if variant == "a":
            decisions[0][field] = invalid_value
        return decisions

    with pytest.raises(Exception) as captured:
        _generate(module, recipe_path, ingredient_path, audit_dir, provider)

    assert getattr(captured.value, "status_code", None) == 502
    assert field in str(captured.value)


def test_提示冲突后人工项未填写不得校验或写回(tmp_path: Path) -> None:
    module = _load_audit_module()
    recipe_path, ingredient_path, audit_dir = _prepare_paths(tmp_path)
    original_bytes = recipe_path.read_bytes()

    def provider(variant: str, batch: list[dict[str, Any]]):
        decisions = _provider(variant, batch)
        if variant == "b":
            decisions[0]["primary_cooking_method"] = "煮"
        return decisions

    _generate(module, recipe_path, ingredient_path, audit_dir, provider)
    with pytest.raises(Exception) as captured:
        module.apply_audit(
            recipe_path,
            ingredient_path,
            audit_dir,
            audit_dir / "manual_review.csv",
            expected_recipe_count=2,
        )

    assert getattr(captured.value, "status_code", None) == 409
    assert "人工审核" in str(captured.value)
    assert recipe_path.read_bytes() == original_bytes


def test_三项属性进入PostgreSQL模型和Neo4j节点() -> None:
    from backend.infrastructure.database.models import Recipe
    from backend.infrastructure.graph.importer import _merge_recipes

    columns = {column.name for column in Recipe.__table__.columns}
    assert FINAL_FIELDS <= columns, (
        f"输入字段={sorted(FINAL_FIELDS)}；实际PostgreSQL字段={sorted(columns)}"
    )

    class RecordingSession:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def run(self, query: str, **params: Any) -> None:
            self.calls.append({"query": query, "params": params})

    session = RecordingSession()
    recipe = SimpleNamespace(
        name="测试菜",
        labels=[],
        total_time_lower_bound_minutes=10,
        dish_type="菜",
        difficulty="简单",
        is_recommendable=True,
        composition_type="荤",
        serving_temperature="热",
        primary_cooking_method="炒",
    )
    _merge_recipes(session, [recipe], [])
    params = session.calls[0]["params"]
    query = session.calls[0]["query"]

    assert params["composition_type"] == "荤"
    assert params["serving_temperature"] == "热"
    assert params["primary_cooking_method"] == "炒"
    assert all(f"r.{field}" in query for field in FINAL_FIELDS)
