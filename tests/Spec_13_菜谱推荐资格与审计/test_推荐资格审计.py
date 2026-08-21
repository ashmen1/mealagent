from __future__ import annotations

import csv
import importlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest


def _recipe(index: int) -> dict[str, Any]:
    return {
        "name": f"菜谱{index:02d}",
        "ingredients": {"番茄": "100g"},
        "atomic_steps": [{"text": "炒熟后装盘"}],
        "dish_type": "菜",
        "labels": ["午餐"],
        "other": {"index": index},
    }


def _write_recipes(path: Path, count: int = 21) -> list[dict[str, Any]]:
    recipes = [_recipe(index) for index in range(count)]
    path.write_text(
        json.dumps(recipes, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return recipes


def _decision(
    recipe: dict[str, Any],
    *,
    value: bool = True,
    confidence: str = "high",
    reason_code: str | None = None,
) -> dict[str, Any]:
    return {
        "recipe_name": recipe["name"],
        "is_recommendable": value,
        "reason_code": reason_code
        or ("finished_item" if value else "preparation_only"),
        "confidence": confidence,
        "reason": "可作为完整成品" if value else "仅为准备操作",
    }


@pytest.fixture
def audit_module():
    try:
        return importlib.import_module(
            "backend.scripts.audit_recipe_recommendability"
        )
    except ModuleNotFoundError as exc:
        pytest.fail(f"缺少推荐资格审计模块：{exc}", pytrace=False)


def test_双提示按20道分批覆盖全部菜谱并输出人工项(
    tmp_path: Path,
    audit_module,
) -> None:
    recipe_path = tmp_path / "recipes.json"
    recipes = _write_recipes(recipe_path)
    audit_dir = tmp_path / "audit"
    calls: list[tuple[str, list[str]]] = []

    def provider(variant: str, batch: list[dict[str, Any]]):
        calls.append((variant, [item["name"] for item in batch]))
        decisions = [_decision(item) for item in batch]
        for decision in decisions:
            if decision["recipe_name"] == "菜谱01" and variant == "b":
                decision.update(
                    is_recommendable=False,
                    reason_code="preparation_only",
                    reason="仅为准备操作",
                )
            if decision["recipe_name"] == "菜谱02" and variant == "a":
                decision["confidence"] = "medium"
        return decisions

    summary = audit_module.generate_audit(
        recipe_path,
        audit_dir,
        provider,
        batch_size=20,
    )

    assert calls == [
        ("a", [item["name"] for item in recipes[:20]]),
        ("b", [item["name"] for item in recipes[:20]]),
        ("a", [recipes[20]["name"]]),
        ("b", [recipes[20]["name"]]),
    ]
    assert summary == {
        "recipe_count": 21,
        "model_call_count": 4,
        "auto_approved_count": 19,
        "manual_review_count": 2,
    }
    resolutions = json.loads(
        (audit_dir / "resolutions.json").read_text(encoding="utf-8")
    )
    assert [item["recipe_name"] for item in resolutions] == [
        item["name"] for item in recipes
    ]
    assert resolutions[0]["status"] == "auto_approved"
    assert resolutions[0]["is_recommendable"] is True
    assert resolutions[1]["status"] == "manual_review"
    assert resolutions[1]["is_recommendable"] is None
    assert resolutions[2]["status"] == "manual_review"

    with (audit_dir / "manual_review.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        rows = list(csv.DictReader(stream))
    assert [row["recipe_name"] for row in rows] == ["菜谱01", "菜谱02"]
    assert all(row["reviewer_value"] == "" for row in rows)


def test_人工审核完成后校验并原子应用且其他字段不变(
    tmp_path: Path,
    audit_module,
) -> None:
    recipe_path = tmp_path / "recipes.json"
    original = _write_recipes(recipe_path, count=2)
    audit_dir = tmp_path / "audit"

    def provider(variant: str, batch: list[dict[str, Any]]):
        return [
            _decision(
                item,
                value=not (variant == "b" and item["name"] == "菜谱01"),
            )
            for item in batch
        ]

    audit_module.generate_audit(
        recipe_path,
        audit_dir,
        provider,
        batch_size=20,
    )
    review_path = audit_dir / "manual_review.csv"
    with review_path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
        fieldnames = list(rows[0])
    rows[0]["reviewer_value"] = "false"
    rows[0]["reviewer_note"] = "人工确认只是准备步骤"
    with review_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    mapping = audit_module.validate_audit(
        recipe_path,
        audit_dir,
        review_path,
        expected_recipe_count=2,
    )
    assert mapping == {"菜谱00": True, "菜谱01": False}

    audit_module.apply_audit(
        recipe_path,
        audit_dir,
        review_path,
        expected_recipe_count=2,
    )
    applied = json.loads(recipe_path.read_text(encoding="utf-8"))
    assert [item["is_recommendable"] for item in applied] == [True, False]
    assert [
        {key: value for key, value in item.items() if key != "is_recommendable"}
        for item in applied
    ] == original
    first_bytes = recipe_path.read_bytes()
    audit_module.apply_audit(
        recipe_path,
        audit_dir,
        review_path,
        expected_recipe_count=2,
    )
    assert recipe_path.read_bytes() == first_bytes


@pytest.mark.parametrize(
    "mutate",
    [
        lambda values: values[:-1],
        lambda values: values + [deepcopy(values[0])],
        lambda values: [
            {**values[0], "recipe_name": "额外菜谱"}, *values[1:]
        ],
        lambda values: [
            {**values[0], "confidence": "unknown"}, *values[1:]
        ],
        lambda values: [
            {
                **values[0],
                "is_recommendable": True,
                "reason_code": "preparation_only",
            },
            *values[1:],
        ],
    ],
)
def test_模型批次覆盖或结构非法返回502(
    tmp_path: Path,
    audit_module,
    mutate,
) -> None:
    recipe_path = tmp_path / "recipes.json"
    _write_recipes(recipe_path, count=2)

    def provider(variant: str, batch: list[dict[str, Any]]):
        decisions = [_decision(item) for item in batch]
        return mutate(decisions) if variant == "a" else decisions

    with pytest.raises(Exception) as captured:
        audit_module.generate_audit(
            recipe_path,
            tmp_path / "audit",
            provider,
            batch_size=20,
        )
    assert getattr(captured.value, "status_code", None) == 502


def test_续跑只调用缺失批次且源基线变化返回409(
    tmp_path: Path,
    audit_module,
) -> None:
    recipe_path = tmp_path / "recipes.json"
    _write_recipes(recipe_path, count=21)
    audit_dir = tmp_path / "audit"
    calls: list[tuple[str, str]] = []

    def provider(variant: str, batch: list[dict[str, Any]]):
        calls.append((variant, batch[0]["name"]))
        if len(calls) == 3:
            raise ConnectionError("模型不可用")
        return [_decision(item) for item in batch]

    with pytest.raises(Exception) as captured:
        audit_module.generate_audit(
            recipe_path,
            audit_dir,
            provider,
            batch_size=20,
        )
    assert getattr(captured.value, "status_code", None) == 503

    resumed_calls: list[tuple[str, str]] = []

    def resumed_provider(variant: str, batch: list[dict[str, Any]]):
        resumed_calls.append((variant, batch[0]["name"]))
        return [_decision(item) for item in batch]

    audit_module.generate_audit(
        recipe_path,
        audit_dir,
        resumed_provider,
        batch_size=20,
        resume=True,
    )
    assert resumed_calls == [("a", "菜谱20"), ("b", "菜谱20")]

    changed = json.loads(recipe_path.read_text(encoding="utf-8"))
    changed[0]["ingredients"] = {"鸡蛋": "2个"}
    recipe_path.write_text(
        json.dumps(changed, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(Exception) as conflict:
        audit_module.generate_audit(
            recipe_path,
            audit_dir,
            resumed_provider,
            batch_size=20,
            resume=True,
        )
    assert getattr(conflict.value, "status_code", None) == 409


def test_两提示资格相同但reason_code不同进入人工审核(
    tmp_path: Path,
    audit_module,
) -> None:
    recipe_path = tmp_path / "recipes.json"
    _write_recipes(recipe_path, count=2)
    audit_dir = tmp_path / "audit"

    def provider(variant: str, batch: list[dict[str, Any]]):
        return [
            _decision(
                item,
                value=False,
                reason_code=(
                    "preparation_only"
                    if variant == "a"
                    else "fragment"
                ),
            )
            for item in batch
        ]

    audit_module.generate_audit(
        recipe_path,
        audit_dir,
        provider,
        batch_size=20,
    )
    resolutions = json.loads(
        (audit_dir / "resolutions.json").read_text(encoding="utf-8")
    )
    assert all(item["status"] == "manual_review" for item in resolutions)
    with (audit_dir / "manual_review.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        rows = list(csv.DictReader(stream))
    assert [row["recipe_name"] for row in rows] == ["菜谱00", "菜谱01"]


def test_人工审核值非法或未填写返回409(
    tmp_path: Path,
    audit_module,
) -> None:
    recipe_path = tmp_path / "recipes.json"
    _write_recipes(recipe_path, count=2)
    audit_dir = tmp_path / "audit"

    def provider(variant: str, batch: list[dict[str, Any]]):
        return [
            _decision(
                item,
                value=not (variant == "b" and item["name"] == "菜谱01"),
            )
            for item in batch
        ]

    audit_module.generate_audit(
        recipe_path,
        audit_dir,
        provider,
        batch_size=20,
    )
    review_path = audit_dir / "manual_review.csv"
    with review_path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
        fieldnames = list(rows[0])
    rows[0]["reviewer_value"] = "yes"
    with review_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    with pytest.raises(Exception) as captured:
        audit_module.validate_audit(
            recipe_path,
            audit_dir,
            review_path,
            expected_recipe_count=2,
        )
    assert getattr(captured.value, "status_code", None) == 409


def test_模型批次只接收四个字段(
    tmp_path: Path,
    audit_module,
) -> None:
    recipe_path = tmp_path / "recipes.json"
    _write_recipes(recipe_path, count=2)

    def provider(variant: str, batch: list[dict[str, Any]]):
        for item in batch:
            assert set(item) == {
                "name",
                "ingredients",
                "atomic_steps",
                "dish_type",
            }
            assert "labels" not in item
        return [_decision(item) for item in batch]

    audit_module.generate_audit(
        recipe_path,
        tmp_path / "audit",
        provider,
        batch_size=20,
    )
