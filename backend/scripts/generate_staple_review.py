"""生成主食构成候选审核文件，不写回正式菜谱。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Callable

from backend.infrastructure.llm import create_chat_model_from_environment
from backend.services.staple_ingredient_review import (
    StapleReviewCandidateService,
    StapleReviewError,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE_PATH = (
    REPO_ROOT / "datas" / "processed" / "Recipes" / "RecipeComplete.json"
)
DEFAULT_REVIEW_PATH = (
    REPO_ROOT / "datas" / "processed" / "Recipes" / "RecipeStapleReview.json"
)
DEFAULT_ENV_PATH = REPO_ROOT / ".env"

OUTPUT_SCHEMA: dict[str, Any] = {
    "title": "StapleIngredientCandidates",
    "description": "一道主食菜谱中承担碳水主食主体或共同主食主体的食材及依据；未识别到时允许空数组",
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ingredient": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["ingredient", "evidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}


def create_staple_review_llm_client(
    env_path: str | Path = DEFAULT_ENV_PATH,
) -> Callable[[str], object]:
    """按项目环境创建严格结构化的主食候选模型调用器。"""

    _load_environment_file(Path(env_path))
    try:
        chat = create_chat_model_from_environment()
        structured_chat = chat.with_structured_output(
            OUTPUT_SCHEMA,
            method="function_calling",
        )
    except Exception as exc:
        raise StapleReviewError(500, f"无法创建主食候选模型：{exc}") from exc

    def invoke(prompt: str) -> object:
        try:
            return structured_chat.invoke(prompt)
        except Exception as exc:
            raise StapleReviewError(502, f"主食候选模型调用失败：{exc}") from exc

    return invoke


def _load_environment_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        raise StapleReviewError(500, f"无法读取环境配置文件 {path}：{exc}") from exc
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        normalized_name = name.strip()
        normalized_value = value.strip().strip('"').strip("'")
        if normalized_name:
            os.environ.setdefault(normalized_name, normalized_value)


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成主食构成候选审核文件")
    parser.add_argument("--recipe-path", type=Path, default=DEFAULT_RECIPE_PATH)
    parser.add_argument("--review-path", type=Path, default=DEFAULT_REVIEW_PATH)
    parser.add_argument("--env-path", type=Path, default=DEFAULT_ENV_PATH)
    parser.add_argument("--workers", type=int, default=8)
    return parser


def main() -> None:
    args = _build_argument_parser().parse_args()
    try:
        service = StapleReviewCandidateService(
            create_staple_review_llm_client(args.env_path),
            max_workers=args.workers,
        )
        result = service.generate(args.recipe_path, args.review_path)
    except StapleReviewError as exc:
        raise SystemExit(
            f"status_code={exc.status_code}, message={exc}"
        ) from exc
    print(
        json.dumps(
            {
                "main_recipe_count": result["main_recipe_count"],
                "review_path": str(result["review_path"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
