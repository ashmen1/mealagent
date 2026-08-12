# Set-Location D:\codes\A_mealagent_v3
# uv run python -m backend.scripts.tag_dish_types

"""为 RecipeComplete.json 的每道菜标注 dish_type（菜/汤/主食/小菜/甜品）。

读取现有 JSON，用真实 LLM 逐道标注，写回原文件（新增 dish_type 字段）。
分批处理并在中途断点时支持断点续跑（已标注的跳过）。
"""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from langchain_anthropic import ChatAnthropic

from backend.infrastructure.llm.langchain_constraints import (
    _read_required_environment_variable,
    build_lowest_reasoning_config,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RECIPE_PATH = REPO_ROOT / "datas" / "processed" / "Recipes" / "RecipeComplete.json"
BATCH_SIZE = 50
WORKERS = 8

DISH_TYPES = ("菜", "汤", "主食", "小菜", "甜品")

PROMPT = """你是菜品分类助手。根据菜名和标签，判断菜品类型，只输出一个词：{types}。
规则：带"汤"字但实际是甜品/点心的（如汤圆、灌汤包）不算汤；饭/面/粥/馒头/包子/饺子/饼/粉算主食；糕点/饼干/甜点算甜品；凉拌/焯水蔬菜类算小菜；炖菜/炒菜/蒸菜/烧烤/烩菜等算菜。
菜名：{name}
标签：{labels}"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="仅标注前 N 道（验证用）")
    args = parser.parse_args()

    with RECIPE_PATH.open(encoding="utf-8") as stream:
        recipes = json.load(stream)

    chat = ChatAnthropic(
        model=_read_required_environment_variable("ANTHROPIC_MODEL"),
        base_url=_read_required_environment_variable("ANTHROPIC_BASE_URL"),
        api_key=_read_required_environment_variable("ANTHROPIC_AUTH_TOKEN"),
        temperature=0,
        timeout=60,
        max_retries=0,
        **build_lowest_reasoning_config(),
    )

    pending = [
        index
        for index, recipe in enumerate(recipes)
        if "dish_type" not in recipe
    ]
    if args.limit:
        pending = pending[: args.limit]
    print(f"总菜数 {len(recipes)}，本次标注 {len(pending)}")

    failed: list[tuple[int, str]] = []
    completed = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        for batch_start in range(0, len(pending), BATCH_SIZE):
            batch = pending[batch_start : batch_start + BATCH_SIZE]
            future_to_index = {
                executor.submit(_tag_one, chat, recipes, index): index
                for index in batch
            }
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    future.result()
                except Exception as exc:
                    failed.append((index, str(exc)[:200]))
                    recipes[index]["dish_type"] = "菜"  # 失败项默认菜，待复核
                completed += 1
            print(f"批次完成：{completed}/{len(pending)}")
            RECIPE_PATH.write_text(
                json.dumps(recipes, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    print(f"完成。失败 {len(failed)} 项：")
    for index, message in failed[:10]:
        print(f"  [{index}] {recipes[index]['name']}: {message}")


def _tag_one(chat: ChatAnthropic, recipes: list[dict], index: int) -> None:
    recipe = recipes[index]
    labels = "/".join(recipe.get("labels") or [])
    response = chat.invoke(
        PROMPT.format(
            types="、".join(DISH_TYPES),
            name=recipe["name"],
            labels=labels,
        )
    )
    content = response.content
    if isinstance(content, list):
        content = content[0].get("text", str(content[0]))
    dish_type = str(content).strip()
    if dish_type not in DISH_TYPES:
        raise ValueError(f"非法类型：{dish_type}")
    recipe["dish_type"] = dish_type


if __name__ == "__main__":
    main()
