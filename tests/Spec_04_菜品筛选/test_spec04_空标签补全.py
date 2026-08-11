from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from backend.scripts import tag_empty_recipe_labels as label_completion


def build_recipe(
    name: str,
    labels: list[str],
    *,
    ingredients: dict[str, str] | None = None,
    dish_type: str = "菜",
) -> dict[str, Any]:
    return {
        "name": name,
        "labels": list(labels),
        "ingredients": ingredients or {"测试食材": "100g"},
        "dish_type": dish_type,
        "total_time_lower_bound_minutes": 20,
        "atomic_steps": [{"text": "将食材煮熟后装盘"}],
    }


def build_classifier_result(
    *,
    meal_tags: list[str] | None = None,
    taste_tags: list[str] | None = None,
    cuisine_tags: list[str] | None = None,
    effect_tags: list[str] | None = None,
    population_tags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "meal_tags": meal_tags or [],
        "meal_evidence": "根据菜品形态判断餐次",
        "taste_tags": taste_tags or [],
        "taste_evidence": "根据调味和烹饪步骤判断口味",
        "cuisine_tags": cuisine_tags or [],
        "cuisine_evidence": "根据菜名和烹饪风格判断菜系",
        "effect_tags": effect_tags or [],
        "effect_evidence": "没有足够证据时留空",
        "population_tags": population_tags or [],
        "population_evidence": "没有足够证据时留空",
    }


def build_review_row(
    recipes: list[dict[str, Any]],
    recipe_name: str,
    *,
    review_status: str = "approved",
) -> dict[str, str]:
    row = {
        field_name: "" for field_name in label_completion.CSV_FIELD_NAMES
    }
    row.update(
        {
            "recipe_name": recipe_name,
            "dish_type": "菜",
            "meal_tags": "晚餐|午餐",
            "meal_confidence": "high",
            "meal_evidence": "人工确认",
            "taste_tags": "清淡",
            "taste_confidence": "high",
            "taste_evidence": "人工确认",
            "review_status": review_status,
            "baseline_nonempty_labels_sha256": (
                label_completion.calculate_nonempty_labels_hash(recipes)
            ),
        }
    )
    return row


def test_LLM候选标签会归一化去重并按标准顺序排列() -> None:
    assert label_completion.normalize_tags(
        ["晚餐", "下午茶", "晚餐"],
        "餐次",
    ) == ["下午茶", "晚餐"]
    assert label_completion.normalize_tags(
        ["麻辣", "咸鲜", "微辣"],
        "口味",
    ) == ["辣", "咸"]
    assert label_completion.normalize_tags(
        ["便秘", "养胃"],
        "功效",
    ) == ["养胃健胃消食"]


def test_高置信度餐次口味和明确菜系可以自动通过() -> None:
    target = build_recipe(
        "意式测试面",
        [],
        ingredients={"意大利面": "100g", "番茄": "50g"},
        dish_type="主食",
    )
    labeled = [
        build_recipe(
            f"相似意面{index}",
            ["午餐", "咸", "西餐风味"],
            ingredients={"意大利面": "100g", "番茄": "50g"},
            dish_type="主食",
        )
        for index in range(3)
    ]

    row = label_completion.build_candidate_row(
        target,
        labeled,
        {"energy_kcal": 120.0},
        lambda prompt: build_classifier_result(
            meal_tags=["午餐"],
            taste_tags=["咸"],
            cuisine_tags=["西餐风味"],
        ),
        "baseline-hash",
    )

    assert row["meal_confidence"] == "high"
    assert row["taste_confidence"] == "high"
    assert row["cuisine_confidence"] == "high"
    assert row["review_required_groups"] == ""
    assert row["review_status"] == "auto_approved"


def test_功效和人群候选无论置信度都必须人工复核() -> None:
    target = build_recipe("红枣甜汤", [], dish_type="汤")
    labeled = [
        build_recipe(
            f"相似甜汤{index}",
            ["下午茶", "甜", "贫血", "老人"],
            dish_type="汤",
        )
        for index in range(3)
    ]

    row = label_completion.build_candidate_row(
        target,
        labeled,
        {"iron_mg": 2.0},
        lambda prompt: build_classifier_result(
            meal_tags=["下午茶"],
            taste_tags=["甜"],
            effect_tags=["贫血"],
            population_tags=["老人"],
        ),
        "baseline-hash",
    )

    assert row["review_status"] == "pending"
    assert row["review_required_groups"] == "功效|人群"
    assert "dg.cnsoc.org" in row["health_guidance_urls"]


def test_审核拒绝待处理状态和未知标签() -> None:
    recipes = [
        build_recipe("待补菜", []),
        build_recipe("已有菜", ["午餐"]),
    ]
    pending_row = build_review_row(
        recipes,
        "待补菜",
        review_status="pending",
    )
    with pytest.raises(label_completion.LabelCompletionError, match="尚未通过"):
        label_completion.validate_review_rows(recipes, [pending_row])

    invalid_row = build_review_row(recipes, "待补菜")
    invalid_row["meal_tags"] = "夜宵"
    with pytest.raises(label_completion.LabelCompletionError, match="未知标签"):
        label_completion.validate_review_rows(recipes, [invalid_row])


def test_审核拒绝菜名覆盖不完整和非空标签基线变化() -> None:
    recipes = [
        build_recipe("待补菜一", []),
        build_recipe("待补菜二", []),
        build_recipe("已有菜", ["午餐"]),
    ]
    one_row = build_review_row(recipes, "待补菜一")
    with pytest.raises(label_completion.LabelCompletionError, match="精确覆盖"):
        label_completion.validate_review_rows(recipes, [one_row])

    rows = [
        build_review_row(recipes, "待补菜一"),
        build_review_row(recipes, "待补菜二"),
    ]
    recipes[-1]["labels"] = ["晚餐"]
    with pytest.raises(label_completion.LabelCompletionError, match="发生变化"):
        label_completion.validate_review_rows(recipes, rows)


def test_应用审核结果只修改原本为空的目标菜() -> None:
    recipes = [
        build_recipe("待补菜", []),
        build_recipe("已有菜", ["早餐", "清淡"]),
    ]
    original_existing_recipe = deepcopy(recipes[1])
    row = build_review_row(recipes, "待补菜")

    result = label_completion.apply_review_rows(recipes, [row])

    assert recipes[0]["labels"] == ["晚餐", "午餐", "清淡"]
    assert recipes[1] == original_existing_recipe
    assert result == {
        "updated_recipes": 1,
        "label_occurrences": 5,
        "remaining_empty_labels": 0,
    }


def test_生成失败会保留已完成审核行供续跑(
    tmp_path: Path,
    monkeypatch,
) -> None:
    recipes = [
        build_recipe("先完成菜", []),
        build_recipe("后失败菜", []),
        build_recipe("已有菜一", ["午餐", "咸"]),
        build_recipe("已有菜二", ["午餐", "咸"]),
        build_recipe("已有菜三", ["午餐", "咸"]),
    ]
    monkeypatch.setattr(label_completion, "EXPECTED_EMPTY_LABEL_COUNT", 2)
    review_path = tmp_path / "review.csv"

    def classify(prompt: str) -> dict[str, Any]:
        if "后失败菜" in prompt:
            raise RuntimeError("模拟LLM失败")
        return build_classifier_result(meal_tags=["午餐"], taste_tags=["咸"])

    with pytest.raises(label_completion.LabelCompletionError, match="已保留成功结果"):
        label_completion.generate_review_file(
            recipes,
            {
                "先完成菜": {"energy_kcal": 100.0},
                "后失败菜": {"energy_kcal": 100.0},
            },
            classify,
            review_path,
            workers=1,
            batch_size=1,
            is_resume=False,
        )

    rows = label_completion.load_review_rows(review_path)
    assert [row["recipe_name"] for row in rows] == ["先完成菜"]
