from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from backend.application import create_constraint_services
from backend.infrastructure.llm.langchain_constraints import (
    create_chat_model_from_environment,
)
from backend.services.answer_composer import (
    AnswerComposerService,
    compose_with_llm,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DIALOGUES_PATH = REPO_ROOT / "datas" / "raw" / "对话用例.json"

DEFAULT_DIALOGUE_IDS = (1, 4, 11, 15, 19, 20)


def run_comparison(
    profile_id: int,
    dialogue_ids: tuple[int, ...],
) -> None:
    """跑指定对话用例,打印模板版与LLM润色版对照,供定夺默认路径。"""

    import json

    with DIALOGUES_PATH.open(encoding="utf-8") as stream:
        dialogues = json.load(stream)
    by_id = {dialogue["id"]: dialogue for dialogue in dialogues}

    chat_model = create_chat_model_from_environment()
    composer = AnswerComposerService()
    with create_constraint_services() as services:
        for dialogue_id in dialogue_ids:
            dialogue = by_id[dialogue_id]
            session_id = services.confirmation.create_session(profile_id)
            for message in dialogue["user_messages"]:
                services.confirmation.submit_turn(session_id, message)
            result = services.recommendation.generate(session_id)
            if not isinstance(result, dict):
                print(f"对话{dialogue_id}: 推荐结果无效", file=sys.stderr)
                continue
            status = result.get("status")
            template = composer.compose(result)
            if status != "recommended":
                print(
                    f"==== 对话{dialogue_id}（{status}，非推荐成功，跳过润色） ====\n"
                    f"{template}\n"
                )
                continue
            started_at = time.perf_counter()
            polished = compose_with_llm(chat_model, result)
            elapsed = round(time.perf_counter() - started_at, 2)
            print(f"==== 对话{dialogue_id}（润色耗时{elapsed}s） ====")
            print("--- 模板版 ---")
            print(template)
            print("--- LLM润色版 ---")
            print(polished)
            print()


def _validate_positive_integer(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name}必须是正整数")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="对比模板回答与LLM润色回答的效果"
    )
    parser.add_argument(
        "--profile-id",
        type=int,
        default=25,
        help="用户档案ID（默认25）",
    )
    parser.add_argument(
        "--dialogue-ids",
        type=int,
        nargs="+",
        default=list(DEFAULT_DIALOGUE_IDS),
        help="对话用例编号列表（默认1 4 11 15 19 20）",
    )
    args = parser.parse_args()
    _validate_positive_integer(args.profile_id, "profile_id")
    for dialogue_id in args.dialogue_ids:
        _validate_positive_integer(dialogue_id, "dialogue_id")
    run_comparison(args.profile_id, tuple(args.dialogue_ids))


if __name__ == "__main__":
    main()
