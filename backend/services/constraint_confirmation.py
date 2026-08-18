from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, Literal, cast

from backend.core.constraint_confirmation_contract import (
    CONFIRMATION_QUESTION,
    Confirmation,
    ConfirmationState,
    ConstraintConfirmationError,
    KnownConstraint,
    PlanningContext,
)
from backend.core.meal_period_contract import CONFIRM_OPTIONS


ConstraintSource = Literal["explicit", "current_time", "default", "derived"]

SOURCE_SUFFIXES: dict[ConstraintSource, str] = {
    "explicit": "",
    "current_time": "（根据当前时间）",
    "default": "（默认）",
    "derived": "（根据各菜品数量合计）",
}

TASTE_DISPLAYS = (
    ("is_sweet", "甜", "不甜"),
    ("is_light", "清淡", "不清淡"),
    ("is_spicy", "辣", "不辣"),
    ("is_salty", "咸", "不咸"),
    ("is_sour", "酸", "不酸"),
)


class ConstraintConfirmationService:
    """统一更新对话、计算生效约束并生成固定展示内容。"""

    def __init__(
        self,
        multi_turn_service: object,
        meal_period_service: object,
    ) -> None:
        required_multi_turn_methods = (
            "create_session",
            "submit_turn",
            "get_session",
        )
        if multi_turn_service is None or any(
            not callable(getattr(multi_turn_service, method, None))
            for method in required_multi_turn_methods
        ):
            raise ConstraintConfirmationError(500, "多轮会话服务无效")
        if meal_period_service is None or not callable(
            getattr(meal_period_service, "resolve", None)
        ):
            raise ConstraintConfirmationError(500, "餐次解析服务无效")
        self._multi_turn_service = multi_turn_service
        self._meal_period_service = meal_period_service

    def create_session(self, profile_id: object) -> int:
        """创建底层多轮会话并返回会话编号。"""

        result = self._call_dependency(
            lambda: self._multi_turn_service.create_session(profile_id)
        )
        if type(result) is not int or result <= 0:
            raise ConstraintConfirmationError(500, "会话创建结果无效")
        return result

    def submit_turn(
        self,
        session_id: object,
        user_message: object,
    ) -> dict[str, Any]:
        """提交一轮消息并返回最新确认状态。"""

        state = self._call_dependency(
            lambda: self._multi_turn_service.submit_turn(
                session_id,
                user_message,
            )
        )
        return self._build_result(
            state,
            identifier_fields=("session_id", "turn_number"),
        )

    def get_session(self, session_id: object) -> dict[str, Any]:
        """读取会话并按本次调用时间重新计算确认状态。"""

        state = self._call_dependency(
            lambda: self._multi_turn_service.get_session(session_id)
        )
        return self._build_result(
            state,
            identifier_fields=("session_id", "profile_id"),
        )

    def _call_dependency(self, action: Callable[[], object]) -> object:
        try:
            return action()
        except ConstraintConfirmationError:
            raise
        except Exception as exc:
            status_code = getattr(exc, "status_code", 500)
            if type(status_code) is not int:
                status_code = 500
            raise ConstraintConfirmationError(
                status_code,
                str(exc),
            ) from exc

    def _build_result(
        self,
        raw_state: object,
        identifier_fields: tuple[str, ...],
    ) -> dict[str, Any]:
        if not isinstance(raw_state, Mapping):
            raise ConstraintConfirmationError(500, "多轮会话状态无效")
        try:
            identifiers = {
                field: raw_state[field]
                for field in identifier_fields
            }
            merged = raw_state["merged_constraints"]
        except (KeyError, TypeError) as exc:
            raise ConstraintConfirmationError(
                500,
                "多轮会话状态缺少必要字段",
            ) from exc

        if merged is None:
            return {**identifiers, **_build_initial_state()}
        if not isinstance(merged, Mapping):
            raise ConstraintConfirmationError(500, "合并约束状态无效")

        resolution = self._call_dependency(
            lambda: self._meal_period_service.resolve(
                merged["meal_periods"]
            )
        )
        try:
            state = _build_confirmation_state(
                cast(dict[str, Any], merged),
                resolution,
            )
        except ConstraintConfirmationError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ConstraintConfirmationError(
                500,
                "约束确认状态构建失败",
            ) from exc
        return {**identifiers, **state}


def _build_initial_state() -> ConfirmationState:
    return {
        "status": "in_progress",
        "merged_constraints": None,
        "planning_context": None,
        "known_constraints": [],
        "confirmation": None,
        "message": None,
    }


def _build_confirmation_state(
    merged: dict[str, Any],
    resolution: object,
) -> ConfirmationState:
    if not isinstance(resolution, Mapping):
        raise ConstraintConfirmationError(500, "餐次解析结果无效")
    resolution_status = resolution.get("status")
    if resolution_status not in {"resolved", "needs_confirmation"}:
        raise ConstraintConfirmationError(500, "餐次解析状态无效")

    context = _build_planning_context(merged, resolution)
    known_constraints = _build_known_constraints(merged, context)
    if resolution_status == "resolved":
        status = "ready_for_planning"
        confirmation = None
    else:
        reason = resolution.get("reason")
        if not isinstance(reason, str) or not reason:
            raise ConstraintConfirmationError(500, "餐次确认原因无效")
        status = "needs_confirmation"
        confirmation = _build_confirmation(reason)

    return {
        "status": status,
        "merged_constraints": merged,
        "planning_context": context,
        "known_constraints": known_constraints,
        "confirmation": confirmation,
        "message": _build_message(known_constraints, status),
    }


def _build_planning_context(
    merged: dict[str, Any],
    resolution: Mapping[str, Any],
) -> PlanningContext:
    if resolution["status"] == "resolved":
        meal_period = resolution["meal_period"]
        meal_period_source = resolution["source"]
        if meal_period not in CONFIRM_OPTIONS:
            raise ConstraintConfirmationError(500, "已解析餐次无效")
        if meal_period_source not in {"explicit", "current_time"}:
            raise ConstraintConfirmationError(500, "餐次来源无效")
    else:
        meal_period = None
        meal_period_source = None

    raw_diner_count = merged["diner_count"]
    if raw_diner_count is None:
        diner_count = 1
        diner_count_source = "default"
    else:
        diner_count = raw_diner_count
        diner_count_source = "explicit"

    total_dish_count, total_source = _resolve_total_dish_count(
        merged["total_dish_count"],
        merged["dishes"],
        diner_count,
    )
    return {
        "meal_period": meal_period,
        "meal_period_source": meal_period_source,
        "diner_count": diner_count,
        "diner_count_source": diner_count_source,
        "total_dish_count": total_dish_count,
        "total_dish_count_source": total_source,
    }


def _resolve_total_dish_count(
    explicit_total: int | None,
    dishes: list[dict[str, Any]],
    diner_count: int,
) -> tuple[int, Literal["explicit", "dish_counts", "default"]]:
    if explicit_total is not None:
        return explicit_total, "explicit"

    counts = [dish["count"] for dish in dishes]
    if all(count is not None for count in counts):
        return sum(cast(int, count) for count in counts), "dish_counts"

    diner_default = diner_count if diner_count <= 3 else diner_count - 1
    minimum_for_groups = sum(
        count if count is not None else 1
        for count in counts
    )
    return max(diner_default, minimum_for_groups), "default"


def _build_known_constraints(
    merged: dict[str, Any],
    context: PlanningContext,
) -> list[KnownConstraint]:
    constraints: list[KnownConstraint] = []
    if context["meal_period"] is not None:
        constraints.append(
            _known_constraint(
                "meal_period",
                "餐次",
                context["meal_period"],
                cast(ConstraintSource, context["meal_period_source"]),
            )
        )
    constraints.append(
        _known_constraint(
            "diner_count",
            "人数",
            f"{context['diner_count']}人",
            cast(ConstraintSource, context["diner_count_source"]),
        )
    )
    total_source: ConstraintSource = (
        "derived"
        if context["total_dish_count_source"] == "dish_counts"
        else cast(ConstraintSource, context["total_dish_count_source"])
    )
    constraints.append(
        _known_constraint(
            "total_dish_count",
            "菜品数量",
            f"{context['total_dish_count']}道",
            total_source,
        )
    )
    constraints.extend(_build_top_constraints(merged))
    for index, dish in enumerate(merged["dishes"]):
        constraints.extend(_build_dish_constraints(index, dish))
    return constraints


def _build_top_constraints(
    merged: dict[str, Any],
) -> list[KnownConstraint]:
    constraints: list[KnownConstraint] = []
    if merged["max_total_time_minutes"] is not None:
        constraints.append(
            _known_constraint(
                "max_total_time_minutes",
                "最长制作时间",
                f"{merged['max_total_time_minutes']}分钟以内",
            )
        )
    if merged["max_difficulty"] is not None:
        constraints.append(
            _known_constraint(
                "max_difficulty",
                "难度",
                str(merged["max_difficulty"]),
            )
        )
    if merged["available_ingredients"]:
        constraints.append(
            _known_constraint(
                "available_ingredients",
                "现有食材",
                _join_values(merged["available_ingredients"]),
            )
        )
    return constraints


def _build_dish_constraints(
    index: int,
    dish: dict[str, Any],
) -> list[KnownConstraint]:
    group_number = index + 1
    path_prefix = f"dishes[{index}]"
    label_prefix = f"菜品组{group_number}"
    constraints: list[KnownConstraint] = []
    if dish["count"] is not None:
        constraints.append(
            _known_constraint(
                f"{path_prefix}.count",
                f"{label_prefix}数量",
                f"{dish['count']}道",
            )
        )
    if dish["dish_type"] != "未指定":
        constraints.append(
            _known_constraint(
                f"{path_prefix}.dish_type",
                f"{label_prefix}类型",
                dish["dish_type"],
            )
        )

    tastes = _display_tastes(dish["taste_preferences"])
    if tastes:
        constraints.append(
            _known_constraint(
                f"{path_prefix}.taste_preferences",
                f"{label_prefix}口味",
                tastes,
            )
        )

    for field, label in (
        ("cuisines", "菜系"),
        ("effects", "功效"),
        ("special_populations", "适用人群"),
    ):
        if dish[field]:
            constraints.append(
                _known_constraint(
                    f"{path_prefix}.{field}",
                    f"{label_prefix}{label}",
                    _join_values(dish[field]),
                )
            )
    if dish["required_ingredients"]:
        constraints.append(
            _known_constraint(
                f"{path_prefix}.required_ingredients",
                f"{label_prefix}必需食材",
                _join_values(
                    requirement["value"]
                    for requirement in dish["required_ingredients"]
                ),
            )
        )
    return constraints


def _known_constraint(
    path: str,
    label: str,
    value: str,
    source: ConstraintSource = "explicit",
) -> KnownConstraint:
    return {
        "path": path,
        "label": label,
        "value": value,
        "source": source,
    }


def _display_tastes(tastes: Mapping[str, bool]) -> str:
    return _join_values(
        positive if tastes[key] else negative
        for key, positive, negative in TASTE_DISPLAYS
        if key in tastes
    )


def _join_values(values: Iterable[object]) -> str:
    return "、".join(str(value) for value in values)


def _build_confirmation(reason: str) -> Confirmation:
    return {
        "reason": reason,
        "options": list(CONFIRM_OPTIONS),
        "question": CONFIRMATION_QUESTION,
    }


def _build_message(
    known_constraints: list[KnownConstraint],
    status: str,
) -> str:
    lines = [
        "已确定：",
        *(
            f"- {item['label']}：{item['value']}"
            f"{SOURCE_SUFFIXES[item['source']]}"
            for item in known_constraints
        ),
    ]
    if status == "needs_confirmation":
        lines.extend(("还需要确认：", CONFIRMATION_QUESTION))
    else:
        lines.append("可以开始规划。")
    return "\n".join(lines)


__all__ = [
    "ConstraintConfirmationError",
    "ConstraintConfirmationService",
]
