# Set-Location D:\codes\A_mealagent_v3
# uv run python -m backend.scripts.generate_e2e_report

"""为随机5用户 × 14条单轮对话生成输入→输出的端到端对照报告（Markdown）。"""

import json
import random
import sys
from pathlib import Path

from backend.application import create_constraint_services
from backend.services import ConstraintIntegrationService

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

DIALOGUES_PATH = REPO_ROOT / "datas" / "raw" / "对话用例.json"
USERS_PATH = (
    REPO_ROOT / "datas" / "processed" / "users"
    / "50个用户健康档案_归一化.json"
)
OUTPUT_PATH = REPO_ROOT / "tests" / "Spec_04_菜品筛选" / "端到端输入输出对照.md"
RANDOM_SEED = 42
SAMPLE_SIZE = 5


def main() -> None:
    with USERS_PATH.open(encoding="utf-8") as stream:
        users = json.load(stream)
    random.seed(RANDOM_SEED)
    sampled_users = random.sample(users, SAMPLE_SIZE)
    user_by_id = {user["id"]: user for user in users}

    with DIALOGUES_PATH.open(encoding="utf-8") as stream:
        dialogues = json.load(stream)
    single_turn = [d for d in dialogues if d["turn_count"] == 1]

    lines: list[str] = [
        "# 端到端输入输出对照（真实 LLM + PostgreSQL + Neo4j）",
        "",
        f"- 样本：随机 {SAMPLE_SIZE} 名用户 × {len(single_turn)} 条单轮对话",
        f"- 链路：用户档案 → 对话约束（LLM）→ 整合 → Neo4j 过滤",
        "",
    ]

    with create_constraint_services() as services:
        integration_service = ConstraintIntegrationService()
        for profile_id in [u["id"] for u in sampled_users]:
            user = user_by_id[profile_id]
            profile_constraints = services.profile.extract(profile_id)
            lines.append(f"## 用户 {profile_id}（{user['性别']}，{user['年龄']}岁）")
            lines.append("")
            lines.append(f"- 口味偏好：{user['口味偏好']}")
            lines.append(f"- 过敏食材：{user['过敏食材'] or '无'}")
            lines.append(f"- 特殊人群：{user['特殊人群'] or '无'}")
            lines.append(f"- 健康需求：{user['健康需求'] or '无'}")
            lines.append(f"- 档案约束：特殊人群={profile_constraints['special_populations']}"
                         f"，口味={profile_constraints['taste_preferences']}"
                         f"，过敏={profile_constraints['allergens']}")
            lines.append("")

            for dialogue in single_turn:
                dialogue_constraints = services.dialogue.extract(dialogue)
                integrated = integration_service.integrate(
                    profile_constraints,
                    dialogue_constraints,
                )
                lines.append(f"### 对话 {dialogue['id']}：{dialogue['user_messages'][0]}")
                lines.append("")
                lines.append(f"- 对话约束：餐次={dialogue_constraints['meal_periods']}"
                             f"，人数={dialogue_constraints['diner_count']}"
                             f"，时限={dialogue_constraints['max_total_time_minutes']}"
                             f"，可用食材={dialogue_constraints['available_ingredients']}")
                lines.append(f"- 整合后菜品组数：{len(integrated['dishes'])}"
                             f"，冲突={integrated['has_conflicts']}")

                if integrated["has_conflicts"]:
                    lines.append(f"- 冲突：{json.dumps(integrated['conflicts'], ensure_ascii=False)}")
                    lines.append("")
                    continue

                filtering_result = services.dish_filtering.filter(integrated)
                lines.append(f"- unmatched过敏词：{filtering_result['unmatched_allergens'] or '无'}")
                for group_index, matches in enumerate(
                    filtering_result["dishes"]
                ):
                    dish = integrated["dishes"][group_index]
                    lines.append(
                        f"  - 组{group_index}（类型={dish['dish_type']}，"
                        f"count={dish['count']}，口味={dish['taste_preferences']}，"
                        f"菜系={dish['cuisines']}，功效={dish['effects']}，"
                        f"人群={dish['special_populations']}，"
                        f"必需食材={dish['required_ingredients']}）"
                    )
                    if matches:
                        lines.append(
                            "    - 候选（"
                            + "、".join(
                                f"{m['recipe_name']}[{'/'.join(m['matched_groups']) or '无组'}]"
                                for m in matches[:8]
                            )
                            + f"{'…' if len(matches) > 8 else ''}"
                            f"（共 {len(matches)} 个）"
                        )
                    else:
                        lines.append("    - 候选：无")
                lines.append("")

    OUTPUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已写入：{OUTPUT_PATH}")


if __name__ == "__main__":
    main()
