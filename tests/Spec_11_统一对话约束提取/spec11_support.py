from __future__ import annotations

import copy
from typing import Any


_UNSET = object()


class FakeLLMClient:
    """记录Prompt并按顺序返回预设结构化结果。"""

    def __init__(
        self,
        response: object = _UNSET,
        *,
        responses: list[object] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.response = response
        self.responses = list(responses) if responses is not None else None
        self.error = error
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
            return copy.deepcopy(self.responses.pop(0))
        if self.response is _UNSET:
            raise AssertionError("FakeLLMClient未配置响应")
        return copy.deepcopy(self.response)


def build_requirement(
    value: str,
    kind: str = "ingredient",
) -> dict[str, str]:
    return {"kind": kind, "value": value}


def build_ingredient_group(
    match: str,
    *items: dict[str, str],
) -> dict[str, Any]:
    return {"match": match, "items": list(items)}


def build_empty_dish() -> dict[str, Any]:
    return {
        "count": None,
        "dish_type": "未指定",
        "taste_preferences": {},
        "cuisines": [],
        "effects": [],
        "special_populations": [],
        "required_ingredient_groups": [],
    }


def build_dish(**overrides: Any) -> dict[str, Any]:
    dish = build_empty_dish()
    dish.update(copy.deepcopy(overrides))
    return dish


def build_turn_result(session_id: int, **overrides: Any) -> dict[str, Any]:
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
    result.update(copy.deepcopy(overrides))
    return result


def build_top_action(
    field: str,
    action: str,
    evidence: str,
) -> dict[str, Any]:
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
    return {
        "field": None,
        "dish_index": dish_index,
        "action": action,
        "evidence": evidence,
    }


__all__ = [
    "FakeLLMClient",
    "build_dish",
    "build_dish_action",
    "build_empty_dish",
    "build_ingredient_group",
    "build_requirement",
    "build_top_action",
    "build_turn_result",
]
