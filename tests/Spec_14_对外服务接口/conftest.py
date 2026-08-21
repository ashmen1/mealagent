from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any


class FakeDependencyError(Exception):
    """模拟依赖服务的带状态码异常。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class FakeChatModel:
    """模拟LLM润色客户端：固定返回文本并记录提示词。"""

    def __init__(self, content: str) -> None:
        self.content = content
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> object:
        self.prompts.append(prompt)
        return SimpleNamespace(content=self.content)


def build_dish_recommendation(
    recipe_name: str,
    texts: tuple[str, ...] = ("符合本次清淡口味偏好。",),
) -> dict[str, Any]:
    return {
        "dish_constraint_index": 0,
        "recipe_name": recipe_name,
        "reasons": [
            {
                "reason_type": "tag_match",
                "matched_group": "口味",
                "matched_tags": ["清淡"],
                "sources": [],
                "text": text,
            }
            for text in texts
        ],
    }


def build_recommendation_reason(
    dishes: tuple[dict[str, Any], ...] | None = None,
    *,
    nutrition_score: int = 12,
    health_constraints: tuple[str, ...] = ("高血压",),
) -> dict[str, Any]:
    menu_reasons: list[dict[str, Any]] = []
    for constraint in health_constraints:
        menu_reasons.append(
            {
                "reason_type": "health_constraint",
                "constraint": constraint,
                "rule": "sodium_upper_bound",
                "sources": [],
                "text": (
                    f"考虑{constraint}需求，本桌菜单规划已将钠摄入上限"
                    "作为必须满足的条件。"
                ),
            }
        )
    menu_reasons.append(
        {
            "reason_type": "nutrition_summary",
            "nutrition_score": nutrition_score,
            "max_score": 16,
            "nutrient_details": [],
            "sources": [],
            "text": (
                "本桌菜单按8项营养指标评分，满分16分，"
                f"本桌得{nutrition_score}分。"
            ),
        }
    )
    return {
        "profile_id": 25,
        "dialogue_id": 101,
        "dish_recommendations": list(dishes) if dishes else [
            build_dish_recommendation("番茄炒蛋"),
            build_dish_recommendation("清蒸鲈鱼"),
        ],
        "menu_reasons": menu_reasons,
    }


def build_confirmation_state(
    status: str = "ready_for_planning",
) -> dict[str, Any]:
    if status == "in_progress":
        return {
            "session_id": 101,
            "profile_id": 25,
            "status": "in_progress",
            "merged_constraints": None,
            "planning_context": None,
            "known_constraints": [],
            "confirmation": None,
            "message": None,
        }
    if status == "needs_confirmation":
        return {
            "session_id": 101,
            "profile_id": 25,
            "status": "needs_confirmation",
            "merged_constraints": {},
            "planning_context": None,
            "known_constraints": [],
            "confirmation": {
                "reason": "未明确餐次",
                "options": ["早餐", "午餐", "晚餐"],
                "question": "请确认这次要安排早餐、午餐还是晚餐？",
            },
            "message": (
                "已确定：\n- 人数：1人（默认）\n还需要确认：\n"
                "请确认这次要安排早餐、午餐还是晚餐？"
            ),
        }
    return {
        "session_id": 101,
        "profile_id": 25,
        "status": "ready_for_planning",
        "merged_constraints": {
            "meal_periods": ["晚餐"],
            "diner_count": 2,
        },
        "planning_context": {
            "meal_period": "晚餐",
            "meal_period_source": "explicit",
            "diner_count": 2,
            "diner_count_source": "explicit",
            "total_dish_count": 2,
            "total_dish_count_source": "explicit",
        },
        "known_constraints": [],
        "confirmation": None,
        "message": None,
    }


def build_generation_result(
    status: str = "recommended",
    **overrides: Any,
) -> dict[str, Any]:
    """构造统一推荐入口完整结果,按终态填充对应字段。"""

    result: dict[str, Any] = {
        "session_id": 101,
        "profile_id": 25,
        "dialogue_id": 101,
        "status": status,
        "confirmation_state": build_confirmation_state(),
        "conflicts": [],
        "unmatched_allergens": [],
        "empty_dish_indexes": [],
        "dish_filtering_result": None,
        "candidate_attempts": [],
        "menu_planning_result": None,
        "recommendation_reason_result": None,
        "quality_warnings": [],
    }
    if status == "needs_confirmation":
        result["confirmation_state"] = build_confirmation_state(
            "needs_confirmation"
        )
    elif status == "in_progress":
        result["confirmation_state"] = build_confirmation_state("in_progress")
    elif status == "constraint_conflict":
        result["conflicts"] = [{"detail": "档案要求不吃海鲜但对话点名海鲜"}]
        result["confirmation_state"] = build_confirmation_state(
            "in_progress"
        )
    elif status == "unmatched_allergen":
        result["unmatched_allergens"] = ["红曲霉"]
        result["confirmation_state"] = build_confirmation_state(
            "in_progress"
        )
    elif status == "empty_candidate":
        result["empty_dish_indexes"] = [0]
        result["confirmation_state"] = build_confirmation_state(
            "in_progress"
        )
    elif status == "planning_infeasible":
        result["candidate_attempts"] = [
            {
                "candidate_limit": None,
                "candidate_counts": [0],
                "outcome": "infeasible",
                "nutrition_score": None,
            }
        ]
        result["confirmation_state"] = build_confirmation_state(
            "in_progress"
        )
    else:
        result["recommendation_reason_result"] = build_recommendation_reason()
        result["menu_planning_result"] = {}
    result.update(copy.deepcopy(overrides))
    return result


class FakeConfirmationService:
    """模拟约束确认服务:记录创建与提交,可注入错误。"""

    def __init__(self, error: BaseException | None = None) -> None:
        self._next_session_id = 101
        self.created: list[int] = []
        self.submitted: list[tuple[int, str]] = []
        self.error = error

    def create_session(self, profile_id: object) -> int:
        if self.error is not None:
            raise self.error
        self.created.append(profile_id)
        session_id = self._next_session_id
        self._next_session_id += 1
        return session_id

    def submit_turn(
        self,
        session_id: object,
        user_message: object,
    ) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        self.submitted.append((session_id, user_message))
        return {
            "session_id": session_id,
            "turn_number": len(self.submitted),
            "status": "ready_for_planning",
            "merged_constraints": {},
            "missing_requirements": [],
        }


class FakeRecommendationService:
    """模拟统一推荐入口:固定返回结果序列,可注入错误。"""

    def __init__(
        self,
        results: list[dict[str, Any]] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.results = list(results) if results else [
            build_generation_result()
        ]
        self.generated: list[int] = []
        self.error = error

    def generate(self, session_id: object) -> dict[str, Any]:
        self.generated.append(session_id)
        if self.error is not None:
            raise self.error
        result = self.results[0]
        if len(self.results) > 1:
            self.results.pop(0)
        return copy.deepcopy(result)


__all__ = [
    "FakeChatModel",
    "FakeConfirmationService",
    "FakeDependencyError",
    "FakeRecommendationService",
    "build_confirmation_state",
    "build_dish_recommendation",
    "build_generation_result",
    "build_recommendation_reason",
]
