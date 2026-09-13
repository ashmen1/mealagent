from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Final, NoReturn


EXPECTED_MAIN_RECIPE_COUNT = 310
REVIEW_FIELDS: Final = frozenset(
    {
        "recipe_name",
        "candidates",
        "reviewed_staple_ingredients",
        "review_status",
    }
)


class StapleReviewError(Exception):
    """主食构成审核流程的可预期错误。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class StapleReviewCandidateService:
    """为主食菜谱生成待人工审核的候选标注。"""

    def __init__(
        self,
        llm_client: Callable[[str], object],
        *,
        max_workers: int = 1,
    ) -> None:
        if not callable(llm_client):
            raise StapleReviewError(500, "主食审核候选模型无效")
        if type(max_workers) is not int or not 1 <= max_workers <= 16:
            raise StapleReviewError(500, "主食候选并发数必须是1到16的整数")
        self._llm_client = llm_client
        self._max_workers = max_workers

    def generate(
        self,
        recipe_path: str | Path,
        review_path: str | Path,
    ) -> dict[str, Any]:
        """生成待审核文件，任一菜谱失败时不覆盖旧文件。"""

        source = _load_json_array(Path(recipe_path), "RecipeComplete.json")
        recipes = _validate_source_recipes(source)
        main_recipes = [
            recipe for recipe in recipes if recipe["dish_type"] == "主食"
        ]
        if len(main_recipes) != EXPECTED_MAIN_RECIPE_COUNT:
            _invalid(
                "当前主食菜谱数必须为"
                f"{EXPECTED_MAIN_RECIPE_COUNT}，实际为{len(main_recipes)}"
            )
        if self._max_workers == 1:
            records = [self._generate_record(recipe) for recipe in main_recipes]
        else:
            with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
                records = list(executor.map(self._generate_record, main_recipes))
        destination = Path(review_path)
        _atomic_write_json(destination, records)
        return {
            "main_recipe_count": len(main_recipes),
            "review_path": destination,
        }

    def _generate_record(
        self,
        recipe: Mapping[str, Any],
    ) -> dict[str, Any]:
        prompt = _build_candidate_prompt(recipe)
        try:
            response = self._llm_client(prompt)
        except Exception as exc:
            raise StapleReviewError(502, f"主食候选生成失败：{exc}") from exc
        candidates = _validate_candidates(
            response,
            set(recipe["ingredients"]),
            recipe["name"],
            status_code=502,
            allow_empty=True,
        )
        return {
            "recipe_name": recipe["name"],
            "candidates": candidates,
            "reviewed_staple_ingredients": None,
            "review_status": "pending",
        }


def apply_staple_review(
    recipe_path: str | Path,
    review_path: str | Path,
) -> int:
    """校验全量人工审核结果并原子写回正式菜谱。"""

    destination = Path(recipe_path)
    source = _load_json_array(destination, "RecipeComplete.json")
    recipes = _validate_source_recipes(source)
    review = _load_json_array(Path(review_path), "RecipeStapleReview.json")
    main_recipes = [row for row in recipes if row["dish_type"] == "主食"]
    if len(main_recipes) != EXPECTED_MAIN_RECIPE_COUNT:
        _invalid(
            "当前主食菜谱数必须为"
            f"{EXPECTED_MAIN_RECIPE_COUNT}，实际为{len(main_recipes)}"
        )
    expected_names = [row["name"] for row in main_recipes]
    if len(review) != len(expected_names):
        _invalid("审核文件缺失或多出主食菜谱")

    reviewed_by_name: dict[str, list[str]] = {}
    actual_names: list[str] = []
    source_by_name = {row["name"]: row for row in main_recipes}
    for index, raw in enumerate(review):
        location = f"RecipeStapleReview.json[{index}]"
        record = _require_mapping(raw, location)
        if set(record) != REVIEW_FIELDS:
            _invalid(f"{location}字段不完整")
        name = _require_nonempty_string(
            record["recipe_name"],
            f"{location}.recipe_name",
        )
        actual_names.append(name)
        recipe = source_by_name.get(name)
        if recipe is None:
            _invalid(f"{location}菜谱不存在")
        _validate_candidates(
            {"candidates": record["candidates"]},
            set(recipe["ingredients"]),
            name,
            status_code=400,
            allow_empty=True,
        )
        if record["review_status"] != "approved":
            _invalid(f"{location}.review_status必须为approved")
        reviewed_by_name[name] = _validate_reviewed_ingredients(
            record["reviewed_staple_ingredients"],
            set(recipe["ingredients"]),
            location,
            allow_empty=True,
        )

    if actual_names != expected_names or len(actual_names) != len(set(actual_names)):
        _invalid("审核菜谱必须完整、不重复且保持源顺序")
    _validate_review_anchors(reviewed_by_name)

    written = []
    for original, recipe in zip(source, recipes, strict=True):
        updated = dict(original)
        updated["staple_ingredients"] = (
            reviewed_by_name[recipe["name"]]
            if recipe["dish_type"] == "主食"
            else []
        )
        written.append(updated)
    _atomic_write_json(destination, written)
    return len(written)


def _validate_source_recipe(raw: object, index: int) -> Mapping[str, Any]:
    location = f"RecipeComplete.json[{index}]"
    recipe = _require_mapping(raw, location)
    _require_nonempty_string(recipe.get("name"), f"{location}.name")
    dish_type = recipe.get("dish_type")
    if not isinstance(dish_type, str):
        _invalid(f"{location}.dish_type必须是字符串")
    if type(recipe.get("is_recommendable")) is not bool:
        _invalid(f"{location}.is_recommendable必须是布尔值")
    ingredients = recipe.get("ingredients")
    if not isinstance(ingredients, Mapping) or not ingredients:
        _invalid(f"{location}.ingredients必须是非空对象")
    if any(not isinstance(item, str) or not item for item in ingredients):
        _invalid(f"{location}.ingredients含非法食材名")
    return recipe


def _validate_source_recipes(source: list[Any]) -> list[Mapping[str, Any]]:
    recipes = [
        _validate_source_recipe(row, index)
        for index, row in enumerate(source)
    ]
    names = [recipe["name"] for recipe in recipes]
    if len(names) != len(set(names)):
        _invalid("RecipeComplete.json菜谱名称不允许重复")
    return recipes


def _build_candidate_prompt(recipe: Mapping[str, Any]) -> str:
    payload = {
        "task": (
            "找出成品中承担主食主体或共同主食主体的全部食材，"
            "并逐项给出直接依据"
        ),
        "rules": [
            "只能原样选择ingredients中的食材名",
            "主食构成严格指承担碳水主食来源的谷物、薯类、淀粉、面点皮坯或复合主食成品",
            "面粉、面团、米饭、面条、谷物、薯类等碳水主食主体应选择",
            "肉、蛋、奶、水、油、果蔬、坚果种子、馅料、配菜、点缀和调味料不得选择，即使其用量大、位于菜名中或承担包裹结构也不得选择",
            "粥饭只选择实际列在ingredients中的谷物、豆类或薯类，不得根据菜名补出缺失的米饭",
            "蛋白饼、蔬菜饼或肉饼若没有谷物、薯类、淀粉或面点皮坯，应返回空候选",
            "允许多个碳水食材共同承担主食构成，但不得把搭配食材扩大为主食来源",
        ],
        "recipe_name": recipe["name"],
        "ingredients": list(recipe["ingredients"]),
        "ingredient_quantities": dict(recipe["ingredients"]),
        "steps": [
            step.get("text")
            for step in recipe.get("atomic_steps", [])
            if isinstance(step, Mapping)
            and isinstance(step.get("text"), str)
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _validate_reviewed_ingredients(
    value: object,
    ingredient_names: set[str],
    location: str,
    *,
    allow_empty: bool = False,
) -> list[str]:
    field_location = f"{location}.reviewed_staple_ingredients"
    if not isinstance(value, list):
        _invalid(f"{field_location}必须是数组")
    if not value and not allow_empty:
        _invalid(f"{field_location}必须是非空数组")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        _invalid(f"{field_location}必须包含非空食材名")
    if len(value) != len(set(value)):
        _invalid(f"{field_location}不允许重复")
    if any(item not in ingredient_names for item in value):
        _invalid(f"{location}含不存在于菜谱的食材")
    return list(value)


def _validate_candidates(
    raw: object,
    ingredient_names: set[str],
    recipe_name: str,
    *,
    status_code: int,
    allow_empty: bool = False,
) -> list[dict[str, str]]:
    response = _require_mapping(
        raw,
        f"{recipe_name}候选结果",
        status_code=status_code,
    )
    if set(response) != {"candidates"}:
        _invalid(f"{recipe_name}候选结果结构非法", status_code=status_code)
    values = response["candidates"]
    if not isinstance(values, list):
        _invalid(f"{recipe_name}候选必须是数组", status_code=status_code)
    if not values and not allow_empty:
        _invalid(f"{recipe_name}候选不能为空", status_code=status_code)
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw_candidate in enumerate(values):
        candidate = _require_mapping(
            raw_candidate,
            f"{recipe_name}候选[{index}]",
            status_code=status_code,
        )
        if set(candidate) != {"ingredient", "evidence"}:
            _invalid(
                f"{recipe_name}候选[{index}]结构非法",
                status_code=status_code,
            )
        ingredient = _require_nonempty_string(
            candidate["ingredient"],
            f"{recipe_name}候选[{index}].ingredient",
            status_code=status_code,
        )
        evidence = _require_nonempty_string(
            candidate["evidence"],
            f"{recipe_name}候选[{index}].evidence",
            status_code=status_code,
        )
        if ingredient not in ingredient_names:
            _invalid(f"{recipe_name}候选食材不存在", status_code=status_code)
        if ingredient in seen:
            _invalid(f"{recipe_name}候选食材重复", status_code=status_code)
        seen.add(ingredient)
        candidates.append({"ingredient": ingredient, "evidence": evidence})
    return candidates


def _validate_review_anchors(reviewed: Mapping[str, list[str]]) -> None:
    if reviewed.get("培根披萨") != ["高筋面粉"]:
        _invalid("培根披萨审核锚点错误")
    if "玉米" not in reviewed.get("蜜汁烤玉米", []):
        _invalid("蜜汁烤玉米审核锚点错误")
    if not {"大米", "红薯"}.issubset(reviewed.get("红薯米饭", [])):
        _invalid("红薯米饭审核锚点错误")


def _load_json_array(path: Path, source_name: str) -> list[Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StapleReviewError(400, f"{source_name}读取失败：{exc}") from exc
    if not isinstance(value, list):
        _invalid(f"{source_name}顶层必须是数组")
    return value


def _atomic_write_json(path: Path, value: object) -> None:
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except OSError as exc:
        cleanup_error: OSError | None = None
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as unlink_exc:
                cleanup_error = unlink_exc
        error = StapleReviewError(500, f"审核文件写入失败：{exc}")
        if cleanup_error is not None:
            error.add_note(f"临时文件清理失败：{cleanup_error}")
        raise error from exc


def _require_mapping(
    value: object,
    location: str,
    *,
    status_code: int = 400,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _invalid(f"{location}必须是对象", status_code=status_code)
    return value


def _require_nonempty_string(
    value: object,
    location: str,
    *,
    status_code: int = 400,
) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid(f"{location}必须是非空字符串", status_code=status_code)
    return value


def _invalid(message: str, *, status_code: int = 400) -> NoReturn:
    raise StapleReviewError(status_code, message)


__all__ = [
    "EXPECTED_MAIN_RECIPE_COUNT",
    "StapleReviewCandidateService",
    "StapleReviewError",
    "apply_staple_review",
]
