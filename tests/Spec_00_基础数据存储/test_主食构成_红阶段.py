from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
from typing import Any, Callable

import pytest
from sqlalchemy import select

from backend.infrastructure.database.models import RecipeIngredient

from .conftest import default_recipe


class FakeStapleLLM:
    """返回固定候选并记录调用，供候选审核服务测试。"""

    def __init__(self, response: object = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> object:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        if self.response is None:
            payload = json.loads(prompt)
            return {
                "candidates": [
                    {
                        "ingredient": payload["ingredients"][0],
                        "evidence": "成品主食主体",
                    }
                ]
            }
        return copy.deepcopy(self.response)


def _load_review_contract() -> tuple[type[Any], Callable[..., int]]:
    module = importlib.import_module(
        "backend.services.staple_ingredient_review"
    )
    return module.StapleReviewCandidateService, module.apply_staple_review


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _build_source_rows() -> list[dict[str, Any]]:
    anchors = [
        ("培根披萨", {"高筋面粉": "200g", "玉米": "30g"}),
        ("蜜汁烤玉米", {"玉米": "200g"}),
        ("红薯米饭", {"大米": "100g", "红薯": "100g"}),
    ]
    rows: list[dict[str, Any]] = []
    for name, ingredients in anchors:
        row = default_recipe()
        row.update(
            name=name,
            dish_type="主食",
            ingredients=ingredients,
            staple_ingredients=[],
        )
        rows.append(row)
    for index in range(307):
        row = default_recipe()
        row.update(name=f"测试主食{index:03d}", dish_type="主食")
        if index == 0:
            row["is_recommendable"] = False
        rows.append(row)
    non_staple = default_recipe()
    non_staple.update(name="测试菜", dish_type="菜", staple_ingredients=[])
    rows.append(non_staple)
    return rows


def _build_approved_review(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reviewed_by_name = {
        "培根披萨": ["高筋面粉"],
        "蜜汁烤玉米": ["玉米"],
        "红薯米饭": ["大米", "红薯"],
    }
    result = []
    for row in rows:
        if row["dish_type"] != "主食":
            continue
        reviewed = (
            []
            if row["name"] in {"测试主食000", "测试主食001"}
            else reviewed_by_name.get(row["name"], ["测试食材"])
        )
        result.append(
            {
                "recipe_name": row["name"],
                "candidates": (
                    [
                        {
                            "ingredient": reviewed[0],
                            "evidence": "人工审核所需候选依据",
                        }
                    ]
                    if reviewed
                    else []
                ),
                "reviewed_staple_ingredients": reviewed,
                "review_status": "approved",
            }
        )
    return result


def test_候选生成端点输出310条待审核记录且保持源顺序(tmp_path) -> None:
    service_type, _ = _load_review_contract()
    source = tmp_path / "RecipeComplete.json"
    review = tmp_path / "RecipeStapleReview.json"
    rows = _build_source_rows()
    _write_json(source, rows)
    service = service_type(FakeStapleLLM())

    result = service.generate(source, review)

    payload = json.loads(review.read_text(encoding="utf-8"))
    assert result == {"main_recipe_count": 310, "review_path": review}
    assert len(payload) == 310
    assert [item["recipe_name"] for item in payload] == [
        row["name"] for row in rows if row["dish_type"] == "主食"
    ]
    assert all(item["review_status"] == "pending" for item in payload)
    assert all(item["reviewed_staple_ingredients"] is None for item in payload)


def test_不可推荐主食允许模型返回空候选(tmp_path) -> None:
    service_type, _ = _load_review_contract()
    source = tmp_path / "RecipeComplete.json"
    review = tmp_path / "RecipeStapleReview.json"
    rows = _build_source_rows()
    _write_json(source, rows)

    def generate_candidate(prompt: str) -> object:
        payload = json.loads(prompt)
        if payload["recipe_name"] == "测试主食000":
            return {"candidates": []}
        return {
            "candidates": [
                {
                    "ingredient": payload["ingredients"][0],
                    "evidence": "成品主食主体",
                }
            ]
        }

    service_type(generate_candidate).generate(source, review)

    payload = json.loads(review.read_text(encoding="utf-8"))
    by_name = {item["recipe_name"]: item for item in payload}
    assert by_name["测试主食000"]["candidates"] == []


def test_候选生成保留可推荐主食空候选供人工复核(tmp_path) -> None:
    service_type, _ = _load_review_contract()
    source = tmp_path / "RecipeComplete.json"
    review = tmp_path / "RecipeStapleReview.json"
    rows = _build_source_rows()
    _write_json(source, rows)

    service_type(FakeStapleLLM(response={"candidates": []})).generate(
        source,
        review,
    )

    payload = json.loads(review.read_text(encoding="utf-8"))
    assert len(payload) == 310
    assert all(item["candidates"] == [] for item in payload)
    assert all(item["review_status"] == "pending" for item in payload)


@pytest.mark.parametrize(
    "response",
    [
        {"candidates": [{"ingredient": "不存在", "evidence": "依据"}]},
        {"candidates": [{"ingredient": "测试食材", "evidence": ""}]},
        {"bad": "shape"},
    ],
    ids=["未知食材", "空证据", "结构非法"],
)
def test_候选结果非法时不覆盖已有审核文件(tmp_path, response) -> None:
    service_type, _ = _load_review_contract()
    source = tmp_path / "RecipeComplete.json"
    review = tmp_path / "RecipeStapleReview.json"
    _write_json(source, _build_source_rows())
    review.write_text("原审核内容", encoding="utf-8")

    with pytest.raises(Exception):
        service_type(FakeStapleLLM(response=response)).generate(source, review)

    assert review.read_text(encoding="utf-8") == "原审核内容"


def test_模型调用失败时不创建审核文件(tmp_path) -> None:
    service_type, _ = _load_review_contract()
    source = tmp_path / "RecipeComplete.json"
    review = tmp_path / "RecipeStapleReview.json"
    _write_json(source, _build_source_rows())

    with pytest.raises(Exception) as captured:
        service_type(FakeStapleLLM(error=RuntimeError("模型失败"))).generate(
            source, review
        )

    assert getattr(captured.value, "status_code", None) == 502
    assert review.exists() is False


def test_审核写回端点只改主食字段并满足三个审核锚点(tmp_path) -> None:
    _, apply_staple_review = _load_review_contract()
    source = tmp_path / "RecipeComplete.json"
    review = tmp_path / "RecipeStapleReview.json"
    rows = _build_source_rows()
    before_without_staple = [
        {key: value for key, value in row.items() if key != "staple_ingredients"}
        for row in rows
    ]
    _write_json(source, rows)
    _write_json(review, _build_approved_review(rows))

    assert apply_staple_review(source, review) == len(rows)

    written = json.loads(source.read_text(encoding="utf-8"))
    assert [
        {key: value for key, value in row.items() if key != "staple_ingredients"}
        for row in written
    ] == before_without_staple
    by_name = {row["name"]: row["staple_ingredients"] for row in written}
    assert by_name["培根披萨"] == ["高筋面粉"]
    assert "玉米" not in by_name["培根披萨"]
    assert by_name["蜜汁烤玉米"] == ["玉米"]
    assert by_name["红薯米饭"] == ["大米", "红薯"]
    assert by_name["测试主食000"] == []
    assert by_name["测试主食001"] == []
    assert by_name["测试菜"] == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda review: review.pop(),
        lambda review: review.append(copy.deepcopy(review[0])),
        lambda review: review[0].update(review_status="pending"),
        lambda review: review[0].update(review_status="rejected"),
        lambda review: review[0].update(
            reviewed_staple_ingredients=["不存在"]
        ),
        lambda review: review[0].update(
            reviewed_staple_ingredients=["高筋面粉", "高筋面粉"]
        ),
        lambda review: review[0].update(
            reviewed_staple_ingredients=["玉米"]
        ),
    ],
    ids=[
        "缺失菜谱",
        "重复菜谱",
        "待审核",
        "审核拒绝",
        "不存在食材",
        "重复食材",
        "审核锚点错误",
    ],
)
def test_审核文件非法时正式菜谱逐字节不变(tmp_path, mutate) -> None:
    _, apply_staple_review = _load_review_contract()
    source = tmp_path / "RecipeComplete.json"
    review_path = tmp_path / "RecipeStapleReview.json"
    rows = _build_source_rows()
    review = _build_approved_review(rows)
    mutate(review)
    _write_json(source, rows)
    _write_json(review_path, review)
    before = source.read_bytes()

    with pytest.raises(Exception) as captured:
        apply_staple_review(source, review_path)

    assert getattr(captured.value, "status_code", None) == 400
    assert source.read_bytes() == before


@pytest.mark.parametrize("is_staple", [True, False])
def test_导入将主食数组映射为非空布尔关系(
    is_staple,
    input_factory,
    db_session,
    invoke_import,
) -> None:
    recipe = default_recipe()
    recipe["dish_type"] = "主食" if is_staple else "菜"
    recipe["staple_ingredients"] = ["测试食材"] if is_staple else []
    paths = input_factory.create(recipes=[recipe])

    invoke_import(paths, db_session)

    relation = db_session.scalar(select(RecipeIngredient))
    assert relation is not None
    assert relation.is_staple_component is is_staple
    column = RecipeIngredient.__table__.c.is_staple_component
    assert column.nullable is False
    assert column.default is None
    assert column.server_default is None


@pytest.mark.parametrize("is_recommendable", [True, False])
def test_已审核主食允许空主食构成并写入false关系(
    is_recommendable,
    input_factory,
    db_session,
    invoke_import,
) -> None:
    recipe = default_recipe()
    recipe.update(
        dish_type="主食",
        is_recommendable=is_recommendable,
        staple_ingredients=[],
    )
    paths = input_factory.create(recipes=[recipe])

    invoke_import(paths, db_session)

    relation = db_session.scalar(select(RecipeIngredient))
    assert relation is not None
    assert relation.is_staple_component is False


@pytest.mark.parametrize(
    "mutate",
    [
        lambda recipe: recipe.pop("staple_ingredients"),
        lambda recipe: recipe.update(staple_ingredients="测试食材"),
        lambda recipe: recipe.update(
            staple_ingredients=["测试食材", "测试食材"]
        ),
        lambda recipe: recipe.update(staple_ingredients=["不存在"]),
    ],
    ids=["字段缺失", "不是数组", "重复", "不存在食材"],
)
def test_非法主食标注返回400且整批不写入(
    mutate,
    input_factory,
    db_session,
    assert_import_error,
) -> None:
    recipe = default_recipe()
    mutate(recipe)
    paths = input_factory.create(recipes=[recipe])

    assert_import_error(paths, db_session, 400)
    assert db_session.scalar(select(RecipeIngredient)) is None
