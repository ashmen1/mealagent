from __future__ import annotations

from typing import Any


class FakeServiceError(Exception):
    """携带状态码的底层服务异常。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class FakeMultiTurnService:
    """记录调用并返回预设会话状态的多轮服务假件。"""

    def __init__(self) -> None:
        self.created_profile_ids: list[object] = []
        self.submitted_turns: list[tuple[object, object]] = []
        self.loaded_session_ids: list[object] = []
        self.create_result = 101
        self.submit_result: object = None
        self.get_result: object = None
        self.create_error: BaseException | None = None
        self.submit_error: BaseException | None = None
        self.get_error: BaseException | None = None

    def create_session(self, profile_id: object) -> int:
        self.created_profile_ids.append(profile_id)
        if self.create_error is not None:
            raise self.create_error
        return self.create_result

    def submit_turn(
        self,
        session_id: object,
        user_message: object,
    ) -> object:
        self.submitted_turns.append((session_id, user_message))
        if self.submit_error is not None:
            raise self.submit_error
        return self.submit_result

    def get_session(self, session_id: object) -> object:
        self.loaded_session_ids.append(session_id)
        if self.get_error is not None:
            raise self.get_error
        return self.get_result


class FakeMealPeriodService:
    """按顺序返回预设餐次解析结果的服务假件。"""

    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.inputs: list[object] = []

    def resolve(self, meal_periods: object) -> object:
        self.inputs.append(meal_periods)
        if not self.responses:
            raise AssertionError("FakeMealPeriodService响应序列已耗尽")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def build_dish(**overrides: Any) -> dict[str, Any]:
    dish = {
        "count": None,
        "dish_type": "未指定",
        "taste_preferences": {},
        "cuisines": [],
        "effects": [],
        "special_populations": [],
        "required_ingredients": [],
    }
    dish.update(overrides)
    return dish


def build_merged(**overrides: Any) -> dict[str, Any]:
    merged = {
        "dialogue_id": 101,
        "meal_periods": [],
        "diner_count": None,
        "total_dish_count": None,
        "max_total_time_minutes": None,
        "max_difficulty": None,
        "available_ingredients": [],
        "dishes": [build_dish()],
        "evidence": {},
    }
    merged.update(overrides)
    return merged


def build_submit_result(merged: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": 101,
        "turn_number": 1,
        "status": "ready_for_planning",
        "merged_constraints": merged,
        "missing_requirements": [],
    }


def build_get_result(
    merged: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "session_id": 101,
        "profile_id": 90001,
        "status": "in_progress" if merged is None else "ready_for_planning",
        "merged_constraints": merged,
        "missing_requirements": [],
    }


def resolved(
    meal_period: str,
    source: str = "explicit",
) -> dict[str, Any]:
    return {
        "status": "resolved",
        "meal_period": meal_period,
        "source": source,
        "reason": None,
        "options": [],
    }


def needs_confirmation(reason: str) -> dict[str, Any]:
    return {
        "status": "needs_confirmation",
        "meal_period": None,
        "source": "current_time",
        "reason": reason,
        "options": ["早餐", "午餐", "晚餐"],
    }


__all__ = [
    "FakeMealPeriodService",
    "FakeMultiTurnService",
    "FakeServiceError",
    "build_dish",
    "build_get_result",
    "build_merged",
    "build_submit_result",
    "needs_confirmation",
    "resolved",
]
