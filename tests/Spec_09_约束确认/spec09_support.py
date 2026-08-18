from __future__ import annotations

from typing import Any


class FakeServiceError(Exception):
    """模拟携带状态码的底层异常。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class FakeMultiTurnService:
    """记录调用并返回预设状态的多轮会话服务假件。"""

    def __init__(self) -> None:
        self.create_calls: list[object] = []
        self.submit_calls: list[tuple[object, object]] = []
        self.get_calls: list[object] = []
        self.create_result: object = 101
        self.submit_result: object = None
        self.get_result: object = None
        self.create_error: BaseException | None = None
        self.submit_error: BaseException | None = None
        self.get_error: BaseException | None = None

    def create_session(self, profile_id: object) -> object:
        self.create_calls.append(profile_id)
        if self.create_error is not None:
            raise self.create_error
        return self.create_result

    def submit_turn(self, session_id: object, user_message: object) -> object:
        self.submit_calls.append((session_id, user_message))
        if self.submit_error is not None:
            raise self.submit_error
        return self.submit_result

    def get_session(self, session_id: object) -> object:
        self.get_calls.append(session_id)
        if self.get_error is not None:
            raise self.get_error
        return self.get_result


class FakeMealPeriodService:
    """按顺序返回餐次结果，不生成任何对话文案。"""

    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[object] = []

    def resolve(self, meal_periods: object) -> object:
        self.calls.append(meal_periods)
        if not self.responses:
            raise AssertionError("餐次假件响应已耗尽")
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


def build_submit_state(
    merged: dict[str, Any],
    turn_number: int = 1,
) -> dict[str, Any]:
    return {
        "session_id": 101,
        "turn_number": turn_number,
        "status": "ready_for_planning",
        "merged_constraints": merged,
        "missing_requirements": [],
    }


def build_get_state(
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


def needs_confirmation(
    reason: str,
    source: str = "current_time",
) -> dict[str, Any]:
    return {
        "status": "needs_confirmation",
        "meal_period": None,
        "source": source,
        "reason": reason,
        "options": ["早餐", "午餐", "晚餐"],
    }


__all__ = [
    "FakeMealPeriodService",
    "FakeMultiTurnService",
    "FakeServiceError",
    "build_dish",
    "build_get_state",
    "build_merged",
    "build_submit_state",
    "needs_confirmation",
    "resolved",
]
