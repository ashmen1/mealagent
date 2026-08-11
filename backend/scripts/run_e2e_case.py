from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from backend.application import create_constraint_services
from backend.services import ConstraintIntegrationService

REPO_ROOT = Path(__file__).resolve().parents[2]
DIALOGUES_PATH = REPO_ROOT / "datas" / "raw" / "对话用例.json"
DEFAULT_CANDIDATE_LIMIT = 10


def run_e2e_case(
    profile_id: int,
    dialogue_id: int,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> dict[str, Any]:
    """运行一组健康档案与单轮对话的菜品筛选链路。"""

    started_at = time.perf_counter()
    validated_profile_id = _validate_positive_integer(
        profile_id,
        "profile_id",
    )
    validated_dialogue_id = _validate_positive_integer(
        dialogue_id,
        "dialogue_id",
    )
    validated_candidate_limit = _validate_positive_integer(
        candidate_limit,
        "candidate_limit",
    )
    _show_progress(1, 6, f"读取单轮对话用例 {validated_dialogue_id}")
    dialogue = load_single_turn_dialogue(validated_dialogue_id)

    _show_progress(2, 6, "初始化 PostgreSQL、LLM 与 Neo4j 服务")
    with create_constraint_services() as services:
        _show_progress(3, 6, f"提取健康档案用户 {validated_profile_id} 的约束")
        profile_constraints = services.profile.extract(validated_profile_id)
        _show_progress(4, 6, "调用 LLM 提取单轮对话约束")
        dialogue_constraints = services.dialogue.extract(dialogue)
        _show_progress(5, 6, "整合健康档案约束与对话约束")
        integrated_constraints = ConstraintIntegrationService().integrate(
            profile_constraints,
            dialogue_constraints,
        )

        common_result: dict[str, Any] = {
            "input": {
                "profile_id": validated_profile_id,
                "dialogue": dialogue,
            },
            "profile_constraints": profile_constraints,
            "dialogue_constraints": dialogue_constraints,
            "integrated_constraints": integrated_constraints,
        }

        # 业务冲突需要用户确认，不能继续执行菜品筛选。
        if integrated_constraints["has_conflicts"]:
            _show_progress(6, 6, "发现业务冲突，跳过 Neo4j 菜品筛选")
            result = {
                "status": "conflict",
                **common_result,
                "conflicts": integrated_constraints["conflicts"],
                "filtering_result": None,
            }
        else:
            _show_progress(6, 6, "查询 Neo4j 并筛选菜品候选")
            filtering_result = services.dish_filtering.filter(
                integrated_constraints
            )
            dish_groups = []
            for dish_index, candidates in enumerate(
                filtering_result["dishes"]
            ):
                dish_groups.append(
                    {
                        "dish_index": dish_index,
                        "request": integrated_constraints["dishes"][
                            dish_index
                        ],
                        "candidate_total": len(candidates),
                        "candidates": candidates[:validated_candidate_limit],
                    }
                )

            result = {
                "status": "success",
                **common_result,
                "filtering_result": {
                    "candidate_limit": validated_candidate_limit,
                    "unmatched_allergens": filtering_result[
                        "unmatched_allergens"
                    ],
                    "dish_groups": dish_groups,
                },
            }

    result["execution_time_seconds"] = round(
        time.perf_counter() - started_at,
        3,
    )
    return result


def load_single_turn_dialogue(
    dialogue_id: int,
    dialogues_path: Path = DIALOGUES_PATH,
) -> dict[str, Any]:
    """按ID读取一条现有单轮对话用例。"""

    validated_dialogue_id = _validate_positive_integer(
        dialogue_id,
        "dialogue_id",
    )
    try:
        with dialogues_path.open(encoding="utf-8") as stream:
            dialogues = json.load(stream)
    except OSError as exc:
        raise RuntimeError(
            f"无法读取对话用例文件：{dialogues_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"对话用例文件不是有效JSON：{dialogues_path}"
        ) from exc

    if not isinstance(dialogues, list) or not all(
        isinstance(item, Mapping) for item in dialogues
    ):
        raise ValueError("对话用例文件顶层必须是对象数组")

    matched_dialogues = [
        item for item in dialogues if item.get("id") == validated_dialogue_id
    ]
    if not matched_dialogues:
        raise ValueError(f"对话用例不存在：{validated_dialogue_id}")
    if len(matched_dialogues) > 1:
        raise ValueError(f"对话用例ID重复：{validated_dialogue_id}")

    dialogue = dict(matched_dialogues[0])
    if dialogue.get("turn_count") != 1:
        raise ValueError(
            f"只支持单轮对话用例：{validated_dialogue_id}"
        )
    return dialogue


def build_argument_parser() -> argparse.ArgumentParser:
    """创建单组端到端链路的命令行参数解析器。"""

    parser = argparse.ArgumentParser(
        description=(
            "运行健康档案、单轮对话约束提取、约束整合与菜品筛选链路"
        )
    )
    parser.add_argument(
        "--profile-id",
        type=_parse_positive_integer,
        required=True,
        help="健康档案用户ID",
    )
    parser.add_argument(
        "--dialogue-id",
        type=_parse_positive_integer,
        required=True,
        help="datas/raw/对话用例.json中的单轮对话ID",
    )
    parser.add_argument(
        "--candidate-limit",
        type=_parse_positive_integer,
        default=DEFAULT_CANDIDATE_LIMIT,
        help=f"每个菜品组展示的候选数量，默认{DEFAULT_CANDIDATE_LIMIT}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """解析命令行参数并以JSON输出链路结果。"""

    arguments = build_argument_parser().parse_args(argv)
    result = run_e2e_case(
        profile_id=arguments.profile_id,
        dialogue_id=arguments.dialogue_id,
        candidate_limit=arguments.candidate_limit,
    )
    print(
        "[完成] 链路运行完成，"
        f"总耗时 {result['execution_time_seconds']:.3f} 秒，开始输出JSON结果",
        file=sys.stderr,
        flush=True,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _parse_positive_integer(value: str) -> int:
    try:
        parsed_value = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须是正整数") from exc
    if parsed_value <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return parsed_value


def _validate_positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name}必须是正整数")
    return value


def _show_progress(step: int, total: int, message: str) -> None:
    """将实时进度写入标准错误流，避免污染JSON标准输出。"""

    print(f"[{step}/{total}] {message}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
