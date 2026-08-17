from __future__ import annotations

from typing import Any


_UNSET = object()


class FakeLLMClient:
    """记录调用信息并返回预设结构化结果的多轮LLM提取器假件。"""

    def __init__(
        self,
        response: object = _UNSET,
        error: BaseException | None = None,
        responses: list[object] | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.responses = list(responses) if responses is not None else None
        self.prompts: list[str] = []

    @property
    def call_count(self) -> int:
        return len(self.prompts)

    def __call__(self, prompt: str) -> Any:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        if self.responses is not None:
            if not self.responses:
                raise AssertionError("FakeLLMClient响应序列已耗尽")
            return self.responses.pop(0)
        if self.response is _UNSET:
            raise AssertionError("FakeLLMClient未配置响应")
        return self.response


def build_empty_dish() -> dict[str, Any]:
    """构建一道全部约束为空的未指定菜品。"""

    return {
        "count": None,
        "dish_type": "未指定",
        "taste_preferences": {},
        "cuisines": [],
        "effects": [],
        "special_populations": [],
        "required_ingredients": [],
    }


def build_dish(**overrides: Any) -> dict[str, Any]:
    dish = build_empty_dish()
    dish.update(overrides)
    return dish


def build_turn_result(session_id: int, **overrides: Any) -> dict[str, Any]:
    """构建一轮完整的九字段约束输出及变更声明。"""

    result = {
        "dialogue_id": session_id,
        "meal_periods": [],
        "diner_count": None,
        "total_dish_count": None,
        "max_total_time_minutes": None,
        "max_difficulty": None,
        "available_ingredients": [],
        "dishes": [build_empty_dish()],
        "evidence": {},
        "change_actions": [],
    }
    result.update(overrides)
    return result


def build_top_action(
    field: str,
    action: str,
    evidence: str,
) -> dict[str, Any]:
    """构建作用于顶层字段的变更声明。"""

    return {
        "field": field,
        "dish_index": None,
        "action": action,
        "evidence": evidence,
    }


def build_dish_action(
    dish_index: int | None,
    action: str,
    evidence: str,
) -> dict[str, Any]:
    """构建作用于Dish的变更声明;dish_index为None表示新增全新菜品组。"""

    return {
        "field": None,
        "dish_index": dish_index,
        "action": action,
        "evidence": evidence,
    }


def build_first_dinner_for_two(session_id: int) -> dict[str, Any]:
    """首轮输出:晚餐、2人、两菜一汤。"""

    return build_turn_result(
        session_id,
        meal_periods=["晚餐"],
        diner_count=2,
        dishes=[
            build_dish(count=2, dish_type="菜"),
            build_dish(count=1, dish_type="汤"),
        ],
        evidence={
            "meal_periods[0]": "晚上",
            "diner_count": "两个人",
            "dishes[0].count": "两菜",
            "dishes[0].dish_type": "两菜",
            "dishes[1].count": "一汤",
            "dishes[1].dish_type": "一汤",
        },
    )


def build_dinner_for_two_dishes(
    session_id: int,
    first_count: int,
) -> list[dict[str, Any]]:
    """返回两菜一汤场景的 dishes 列表,可覆盖第一道菜的数量。"""

    dishes = build_first_dinner_for_two(session_id)["dishes"]
    return [
        {**dishes[0], "count": first_count},
        dishes[1],
    ]


def build_inherited_dinner_for_two(
    session_id: int,
    **overrides: Any,
) -> dict[str, Any]:
    """继承首轮两菜一汤状态的后续轮输出,可覆盖部分字段。"""

    result = build_first_dinner_for_two(session_id)
    result["evidence"] = {}
    result["change_actions"] = []
    result.update(overrides)
    return result


__all__ = [
    "FakeLLMClient",
    "build_dinner_for_two_dishes",
    "build_dish",
    "build_dish_action",
    "build_empty_dish",
    "build_first_dinner_for_two",
    "build_inherited_dinner_for_two",
    "build_top_action",
    "build_turn_result",
]
