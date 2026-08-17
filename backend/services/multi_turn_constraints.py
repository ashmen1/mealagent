from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Collection, Iterable, Mapping
from typing import Any

from sqlalchemy.orm import Session

from backend.core.dialogue_constraint_contract import (
    CUISINES,
    DISH_FIELDS,
    DISH_TYPES,
    EFFECTS,
    INGREDIENT_CONCEPTS,
    INGREDIENT_REQUIREMENT_FIELDS,
    INGREDIENT_REQUIREMENT_KINDS,
    MEAL_PERIODS,
    SPECIAL_POPULATIONS,
    TASTE_PREFERENCES,
)
from backend.core.multi_turn_contract import (
    CHANGEABLE_TOP_FIELDS,
    CHANGE_ACTION_FIELDS,
    CHANGE_ACTIONS,
    MISSING_REQUIREMENTS,
    MULTI_TURN_CONSTRAINT_FIELDS,
    MULTI_TURN_TOP_LEVEL_FIELDS,
    MultiTurnConstraintError,
    SCALAR_FIELDS,
    SESSION_STATUSES,
)
from backend.infrastructure.database.dialogue_state_repository import (
    DialogueStateRepositoryError,
    insert_dialogue_session,
    insert_dialogue_turn,
    load_dialogue_session,
    next_turn_number,
    update_dialogue_session_state,
)
from backend.infrastructure.database.ingredient_repository import (
    IngredientRepositoryError,
    load_ingredient_constraint_values,
)
from backend.infrastructure.database.profile_repository import (
    ProfileRepositoryError,
    load_user_profile,
)
from backend.services.meal_period_resolution import MealPeriodResolutionError


SessionFactory = Callable[[], Session]

# 状态与缺失要素的具名常量,取自契约枚举,避免魔法字符串
_, NEEDS_CONFIRMATION, READY_FOR_PLANNING = SESSION_STATUSES
MISSING_DINER, MISSING_DISH_TYPE = MISSING_REQUIREMENTS


class MultiTurnConstraintService:
    """管理多轮约束会话:状态落库、LLM介导合并与完整性判定。"""

    def __init__(
        self,
        session_factory: SessionFactory,
        llm_client: Callable[[str], object],
        meal_period_service: object,
    ) -> None:
        if not callable(session_factory):
            raise MultiTurnConstraintError(500, "Session工厂无效")
        if not callable(llm_client):
            raise MultiTurnConstraintError(500, "LLM约束提取器无效")
        if meal_period_service is None or not callable(
            getattr(meal_period_service, "resolve", None)
        ):
            raise MultiTurnConstraintError(500, "餐次解析服务无效")
        self._session_factory = session_factory
        self._llm_client = llm_client
        self._meal_period_service = meal_period_service

    def create_session(self, profile_id: object) -> int:
        """为指定用户档案创建会话,返回会话id。"""

        validated_profile_id = _validate_positive_integer(
            profile_id,
            "profile_id",
        )
        session = self._open_session()
        with session:
            try:
                profile = load_user_profile(session, validated_profile_id)
            except ProfileRepositoryError as exc:
                raise MultiTurnConstraintError(500, str(exc)) from exc
            if profile is None:
                raise MultiTurnConstraintError(409, "用户档案不存在")
            try:
                session_id = insert_dialogue_session(
                    session,
                    validated_profile_id,
                )
                session.commit()
                return session_id
            except DialogueStateRepositoryError as exc:
                raise MultiTurnConstraintError(500, str(exc)) from exc

    def submit_turn(
        self,
        session_id: object,
        user_message: object,
    ) -> dict[str, Any]:
        """提交一轮对话,返回合并后的会话状态。"""

        validated_session_id = _validate_positive_integer(
            session_id,
            "session_id",
        )
        if not isinstance(user_message, str) or not user_message.strip():
            raise MultiTurnConstraintError(
                400,
                "user_message必须是非空字符串",
            )

        session = self._open_session()
        with session:
            try:
                return self._submit_turn_in_session(
                    session,
                    validated_session_id,
                    user_message,
                )
            except MultiTurnConstraintError:
                session.rollback()
                raise

    def get_session(self, session_id: object) -> dict[str, Any]:
        """读取当前会话完整状态。"""

        validated_session_id = _validate_positive_integer(
            session_id,
            "session_id",
        )
        session = self._open_session()
        with session:
            row = self._load_session_row(session, validated_session_id)
            if row is None:
                raise MultiTurnConstraintError(400, "会话不存在")
            return _build_state(row)

    def _open_session(self) -> Session:
        try:
            session = self._session_factory()
        except Exception as exc:
            raise MultiTurnConstraintError(
                500,
                "数据库 Session 创建失败",
            ) from exc
        if not isinstance(session, Session):
            raise MultiTurnConstraintError(500, "数据库 Session 无效")
        return session

    def _load_session_row(self, session: Session, session_id: int):
        try:
            return load_dialogue_session(session, session_id)
        except DialogueStateRepositoryError as exc:
            raise MultiTurnConstraintError(500, str(exc)) from exc

    def _submit_turn_in_session(
        self,
        session: Session,
        session_id: int,
        user_message: str,
    ) -> dict[str, Any]:
        try:
            session_row = load_dialogue_session(
                session,
                session_id,
                for_update=True,
            )
        except DialogueStateRepositoryError as exc:
            raise MultiTurnConstraintError(500, str(exc)) from exc
        if session_row is None:
            raise MultiTurnConstraintError(400, "会话不存在")

        try:
            turn_number = next_turn_number(session, session_id)
            ingredient_names, ingredient_categories = (
                load_ingredient_constraint_values(session)
            )
        except (DialogueStateRepositoryError, IngredientRepositoryError) as exc:
            raise MultiTurnConstraintError(500, str(exc)) from exc

        previous = session_row.merged_constraints
        prompt = _build_prompt(
            session_id,
            user_message,
            previous,
            ingredient_categories,
        )
        try:
            merged = _extract_and_merge(
                prompt,
                self._llm_client,
                session_id,
                previous,
                user_message,
                ingredient_names,
                ingredient_categories,
            )
        except MultiTurnConstraintError as exc:
            if exc.status_code != 502:
                raise
            # LLM 概率输出偶发违例,重试一次;再次违例仍按 502 抛出
            merged = _extract_and_merge(
                prompt,
                self._llm_client,
                session_id,
                previous,
                user_message,
                ingredient_names,
                ingredient_categories,
            )

        status, missing = _evaluate_completeness(
            merged,
            self._meal_period_service,
        )
        try:
            insert_dialogue_turn(
                session,
                session_id,
                turn_number,
                user_message,
            )
            update_dialogue_session_state(
                session,
                session_row,
                merged,
                status,
            )
            session.commit()
        except DialogueStateRepositoryError as exc:
            raise MultiTurnConstraintError(500, str(exc)) from exc

        return {
            "session_id": session_id,
            "turn_number": turn_number,
            "status": status,
            "merged_constraints": merged,
            "missing_requirements": missing,
        }


def _validate_positive_integer(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise MultiTurnConstraintError(400, f"{name}必须是正整数")
    return value


def _build_state(session_row: object) -> dict[str, Any]:
    """由会话行构造返回状态;缺失要素由合并约束实时推导。"""

    merged = session_row.merged_constraints
    return {
        "session_id": session_row.id,
        "profile_id": session_row.profile_id,
        "status": session_row.status,
        "merged_constraints": merged,
        "missing_requirements": _missing_requirements(merged),
    }


def _missing_requirements(merged: dict[str, Any] | None) -> list[str]:
    """按固定顺序推导缺失要素;尚无合并约束时全部缺失。"""

    if merged is None:
        return list(MISSING_REQUIREMENTS)
    missing: list[str] = []
    if merged["diner_count"] is None:
        missing.append(MISSING_DINER)
    if all(dish["dish_type"] == "未指定" for dish in merged["dishes"]):
        missing.append(MISSING_DISH_TYPE)
    return missing


def _evaluate_completeness(
    merged: dict[str, Any],
    meal_period_service: object,
) -> tuple[str, list[str]]:
    """硬门槛判定:餐次唯一 resolved 即 ready,否则 needs_confirmation。"""

    try:
        resolution = meal_period_service.resolve(merged["meal_periods"])
    except MealPeriodResolutionError as exc:
        raise MultiTurnConstraintError(500, str(exc)) from exc

    if resolution["status"] == "resolved":
        status = READY_FOR_PLANNING
    else:
        status = NEEDS_CONFIRMATION
    return status, _missing_requirements(merged)


def _extract_and_merge(
    prompt: str,
    llm_client: Callable[[str], object],
    session_id: int,
    previous: dict[str, Any] | None,
    user_message: str,
    ingredient_names: set[str],
    ingredient_categories: set[str],
) -> dict[str, Any]:
    """调用LLM一次,完成结构校验、数字归一化与状态合并。"""

    try:
        result = llm_client(prompt)
    except (TimeoutError, ConnectionError) as exc:
        raise MultiTurnConstraintError(
            503,
            "LLM服务请求超时或不可用",
        ) from exc

    if not isinstance(result, dict):
        raise MultiTurnConstraintError(502, "LLM必须返回结构化对象")
    normalized = _normalize_llm_numeric_fields(result)
    output = _validate_turn_output(
        normalized,
        session_id,
        ingredient_names,
        ingredient_categories,
    )
    return _merge_turn_output(output, previous, user_message)


def _normalize_llm_numeric_fields(
    result: dict[str, Any],
) -> dict[str, Any]:
    """归一化 LLM 输出的数字字段,同 Spec_02 的确定性归一规则。"""

    for field in (
        "diner_count",
        "total_dish_count",
        "max_total_time_minutes",
    ):
        if field in result:
            result[field] = _normalize_optional_integer(result[field])
    for dish in result.get("dishes", []):
        if isinstance(dish, dict) and "count" in dish:
            dish["count"] = _normalize_optional_integer(dish["count"])
    return result


def _normalize_optional_integer(value: object) -> object:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
        if stripped.lower() in {"null", "none"}:
            return None
    return value


def _validate_turn_output(
    result: dict[str, Any],
    session_id: int,
    ingredient_names: set[str],
    ingredient_categories: set[str],
) -> dict[str, Any]:
    _require_exact_fields(result, MULTI_TURN_TOP_LEVEL_FIELDS, "顶层")

    if type(result["dialogue_id"]) is not int:
        _invalid_response("dialogue_id必须是整数")
    if result["dialogue_id"] != session_id:
        _invalid_response("dialogue_id必须等于会话id")

    _validate_optional_positive_integer(result["diner_count"], "diner_count")
    _validate_optional_positive_integer(
        result["total_dish_count"],
        "total_dish_count",
    )
    _validate_optional_positive_integer(
        result["max_total_time_minutes"],
        "max_total_time_minutes",
    )
    _validate_string_array(result["meal_periods"], "meal_periods")
    _require_allowed_values(result["meal_periods"], MEAL_PERIODS, "meal_periods")
    _validate_string_array(
        result["available_ingredients"],
        "available_ingredients",
    )
    _require_allowed_values(
        result["available_ingredients"],
        ingredient_names,
        "available_ingredients",
    )

    dishes = result["dishes"]
    if not isinstance(dishes, list) or not dishes:
        _invalid_response("dishes至少包含一项")
    _require_no_duplicates(dishes, "dishes")
    for index, dish in enumerate(dishes):
        _validate_dish(
            dish,
            index,
            ingredient_names,
            ingredient_categories,
        )
    _validate_dish_count_consistency(
        result["total_dish_count"],
        dishes,
    )

    if result["max_difficulty"] not in {None, "简单", "中等"}:
        _invalid_response("max_difficulty只允许简单、中等或null")

    evidence = result["evidence"]
    if not isinstance(evidence, dict):
        _invalid_response("evidence必须是对象")
    for path, fragment in evidence.items():
        if not isinstance(path, str) or not isinstance(fragment, str):
            _invalid_response("evidence的键和值必须是字符串")

    _validate_change_actions(result["change_actions"])
    return result


def _validate_dish(
    dish: object,
    dish_index: int,
    ingredient_names: set[str],
    ingredient_categories: set[str],
) -> None:
    location = f"dishes[{dish_index}]"
    if not isinstance(dish, dict):
        _invalid_response(f"{location}必须是对象")
    _require_exact_fields(dish, DISH_FIELDS, location)

    _validate_optional_positive_integer(dish["count"], f"{location}.count")
    if not isinstance(dish["dish_type"], str):
        _invalid_response(f"{location}.dish_type必须是字符串")
    if dish["dish_type"] not in DISH_TYPES:
        _invalid_response(f"{location}.dish_type不在允许值中")

    tastes = dish["taste_preferences"]
    if not isinstance(tastes, dict):
        _invalid_response(f"{location}.taste_preferences必须是对象")
    if not set(tastes).issubset(TASTE_PREFERENCES):
        _invalid_response(f"{location}.taste_preferences包含非法键")
    if any(type(value) is not bool for value in tastes.values()):
        _invalid_response(f"{location}.taste_preferences值必须是布尔值")

    for field, allowed_values in (
        ("cuisines", CUISINES),
        ("effects", EFFECTS),
        ("special_populations", SPECIAL_POPULATIONS),
    ):
        value = dish[field]
        _validate_string_array(value, f"{location}.{field}")
        _require_allowed_values(value, allowed_values, f"{location}.{field}")

    requirements = dish["required_ingredients"]
    if not isinstance(requirements, list):
        _invalid_response(f"{location}.required_ingredients必须是数组")
    _require_no_duplicates(requirements, f"{location}.required_ingredients")
    for requirement_index, requirement in enumerate(requirements):
        _validate_ingredient_requirement(
            requirement,
            f"{location}.required_ingredients[{requirement_index}]",
            ingredient_names,
            ingredient_categories,
        )


def _validate_ingredient_requirement(
    requirement: object,
    location: str,
    ingredient_names: set[str],
    ingredient_categories: set[str],
) -> None:
    if not isinstance(requirement, dict):
        _invalid_response(f"{location}必须是对象")
    _require_exact_fields(
        requirement,
        INGREDIENT_REQUIREMENT_FIELDS,
        location,
    )
    kind = requirement["kind"]
    value = requirement["value"]
    if not isinstance(kind, str) or kind not in INGREDIENT_REQUIREMENT_KINDS:
        _invalid_response(f"{location}.kind不在允许值中")
    if not isinstance(value, str):
        _invalid_response(f"{location}.value必须是字符串")

    values_by_kind = {
        "ingredient": ingredient_names,
        "category": ingredient_categories,
        "concept": INGREDIENT_CONCEPTS,
    }
    if value not in values_by_kind[kind]:
        _invalid_response(f"{location}的kind与value不匹配")


def _validate_dish_count_consistency(
    total_dish_count: int | None,
    dishes: list[dict[str, Any]],
) -> None:
    """校验整桌总数与各菜品组显式或最低数量一致。"""

    if total_dish_count is None:
        return
    explicit_sum = sum(
        dish["count"] for dish in dishes if dish["count"] is not None
    )
    null_group_count = sum(dish["count"] is None for dish in dishes)
    if explicit_sum + null_group_count > total_dish_count:
        _invalid_response("菜品组最低数量超过整桌菜品总数")
    if null_group_count == 0 and explicit_sum != total_dish_count:
        _invalid_response("全部菜品组数量明确时必须等于整桌菜品总数")


def _validate_change_actions(actions: object) -> None:
    if not isinstance(actions, list):
        _invalid_response("change_actions必须是数组")
    for index, action in enumerate(actions):
        location = f"change_actions[{index}]"
        if not isinstance(action, dict):
            _invalid_response(f"{location}必须是对象")
        _require_exact_fields(action, CHANGE_ACTION_FIELDS, location)

        field = action["field"]
        dish_index = action["dish_index"]
        if field is None and dish_index is None:
            # 新增全新菜品组:两者均空,只允许 add
            if action["action"] != "add":
                _invalid_response(
                    f"{location}的field与dish_index同时为空时action必须是add"
                )
        if field is not None and dish_index is not None:
            _invalid_response(f"{location}的field与dish_index不能同时填写")
        if field is not None and field not in CHANGEABLE_TOP_FIELDS:
            _invalid_response(f"{location}.field不在允许值中")
        if dish_index is not None and (
            type(dish_index) is not int or dish_index < 0
        ):
            _invalid_response(f"{location}.dish_index必须是非负整数")
        if action["action"] not in CHANGE_ACTIONS:
            _invalid_response(f"{location}.action不在允许值中")
        if not isinstance(action["evidence"], str):
            _invalid_response(f"{location}.evidence必须是字符串")


def _merge_turn_output(
    output: dict[str, Any],
    previous: dict[str, Any] | None,
    user_message: str,
) -> dict[str, Any]:
    """合并本轮输出:首轮直接采纳,后续轮重放校验后采纳。"""

    constraints = {
        key: output[key] for key in MULTI_TURN_CONSTRAINT_FIELDS
    }
    if previous is None:
        if output["change_actions"]:
            _invalid_response("首轮不允许变更声明")
        expected_paths = _collect_leaf_paths(constraints)
        if set(output["evidence"]) != expected_paths:
            _invalid_response("首轮evidence路径必须与所有非空约束精确对应")
        for path, fragment in output["evidence"].items():
            _require_evidence_fragment(fragment, user_message, path)
        merged_evidence = dict(output["evidence"])
    else:
        replayed = _replay_actions(
            previous,
            output["change_actions"],
            output,
            user_message,
        )
        if not _constraints_equal(replayed, output):
            _invalid_response("变更声明重放结果与输出不一致")
        merged_evidence = _merge_evidence(previous, output, user_message)

    constraints["evidence"] = merged_evidence
    return constraints


def _replay_actions(
    previous: dict[str, Any],
    actions: list[dict[str, Any]],
    output: dict[str, Any],
    user_message: str,
) -> dict[str, Any]:
    """对上一状态逐条应用变更声明,返回重放结果。"""

    replayed = copy.deepcopy(previous)
    seen_fields: set[str] = set()
    seen_dish_indices: set[int] = set()

    for action in actions:
        _require_evidence_fragment(
            action["evidence"],
            user_message,
            "change_actions.evidence",
        )
        field = action["field"]
        dish_index = action["dish_index"]
        kind = action["action"]

        if field is not None:
            if field in seen_fields:
                _invalid_response(f"字段{field}出现多条声明")
            seen_fields.add(field)
            old_value = replayed[field]
            new_value = output[field]
            if field == "max_difficulty":
                if kind == "add":
                    _invalid_response("max_difficulty不允许add")
                if kind == "remove" and new_value is not None:
                    _invalid_response("max_difficulty remove要求输出为null")
            elif field in SCALAR_FIELDS:
                if kind == "add":
                    if (
                        old_value is None
                        or new_value is None
                        or new_value <= old_value
                    ):
                        _invalid_response(
                            f"标量add要求旧值非空且新值大于旧值:{field}"
                        )
                elif kind == "remove":
                    if new_value is not None:
                        _invalid_response(
                            f"标量remove要求输出为null:{field}"
                        )
            else:
                if kind == "add":
                    if not _is_ordered_subset(old_value, new_value):
                        _invalid_response(
                            f"数组add要求输出包含旧数组全部元素:{field}"
                        )
                elif kind == "remove":
                    if not _is_ordered_subset(new_value, old_value):
                        _invalid_response(
                            f"数组remove要求输出是旧数组的子集:{field}"
                        )
            replayed[field] = copy.deepcopy(new_value)
            continue

        if dish_index is None:
            # 新增菜品组:取输出中位于当前重放长度处的新Dish,追加到末尾
            position = len(replayed["dishes"])
            if position >= len(output["dishes"]):
                _invalid_response("新增菜品组时输出缺少对应Dish")
            replayed["dishes"].append(
                copy.deepcopy(output["dishes"][position])
            )
            continue

        if dish_index in seen_dish_indices:
            _invalid_response(f"Dish索引{dish_index}出现多条声明")
        seen_dish_indices.add(dish_index)
        if dish_index >= len(replayed["dishes"]):
            _invalid_response(f"Dish索引{dish_index}不存在")

        if kind == "remove":
            del replayed["dishes"][dish_index]
            continue
        if dish_index >= len(output["dishes"]):
            _invalid_response(f"Dish索引{dish_index}在输出中不存在")

        if kind == "add":
            old_count = replayed["dishes"][dish_index]["count"]
            new_count = output["dishes"][dish_index]["count"]
            if (
                old_count is None
                or new_count is None
                or new_count <= old_count
            ):
                _invalid_response(
                    f"Dish add要求旧count非空且新count大于旧值:"
                    f"dishes[{dish_index}]"
                )
            replayed["dishes"][dish_index] = copy.deepcopy(
                output["dishes"][dish_index]
            )
        else:
            replayed["dishes"][dish_index] = copy.deepcopy(
                output["dishes"][dish_index]
            )

    return replayed


def _constraints_equal(
    replayed: dict[str, Any],
    output: dict[str, Any],
) -> bool:
    for key in MULTI_TURN_CONSTRAINT_FIELDS:
        if key == "evidence":
            continue
        if replayed[key] != output[key]:
            return False
    return True


def _merge_evidence(
    previous: dict[str, Any],
    output: dict[str, Any],
    user_message: str,
) -> dict[str, str]:
    """未变更字段继承原轮证据,新增或变更字段采用本轮证据。"""

    merged: dict[str, str] = {}
    output_constraints = {
        key: output[key] for key in MULTI_TURN_CONSTRAINT_FIELDS
    }
    previous_paths = _collect_leaf_paths(previous)
    for path in _collect_leaf_paths(output_constraints):
        if (
            path in previous_paths
            and _value_at(previous, path) == _value_at(output, path)
        ):
            merged[path] = previous["evidence"][path]
        else:
            fragment = output["evidence"].get(path)
            _require_evidence_fragment(fragment, user_message, path)
            merged[path] = fragment
    return merged


def _collect_leaf_paths(constraints: Mapping[str, Any]) -> set[str]:
    """收集所有非空约束的叶子路径,同 Spec_02 的规则。"""

    paths: set[str] = set()
    paths.update(
        f"meal_periods[{index}]"
        for index in range(len(constraints["meal_periods"]))
    )
    if constraints["diner_count"] is not None:
        paths.add("diner_count")
    if constraints["total_dish_count"] is not None:
        paths.add("total_dish_count")
    if constraints["max_total_time_minutes"] is not None:
        paths.add("max_total_time_minutes")
    if constraints["max_difficulty"] is not None:
        paths.add("max_difficulty")
    paths.update(
        f"available_ingredients[{index}]"
        for index in range(len(constraints["available_ingredients"]))
    )

    for dish_index, dish in enumerate(constraints["dishes"]):
        prefix = f"dishes[{dish_index}]"
        if dish["count"] is not None:
            paths.add(f"{prefix}.count")
        if dish["dish_type"] != "未指定":
            paths.add(f"{prefix}.dish_type")
        paths.update(
            f"{prefix}.taste_preferences.{taste}"
            for taste in dish["taste_preferences"]
        )
        for field in ("cuisines", "effects", "special_populations"):
            paths.update(
                f"{prefix}.{field}[{index}]"
                for index in range(len(dish[field]))
            )
        paths.update(
            f"{prefix}.required_ingredients[{index}].value"
            for index in range(len(dish["required_ingredients"]))
        )
    return paths


def _value_at(constraints: Mapping[str, Any], path: str) -> object:
    """按叶子路径读取约束值。"""

    value: Any = constraints
    for part in path.split("."):
        match = re.fullmatch(r"(\w+)(?:\[(\d+)\])?", part)
        if match is None:
            raise KeyError(path)
        value = value[match.group(1)]
        index = match.group(2)
        if index is not None:
            value = value[int(index)]
    return value


def _require_evidence_fragment(
    fragment: object,
    user_message: str,
    location: str,
) -> None:
    if not isinstance(fragment, str) or not fragment.strip():
        _invalid_response(f"evidence不是连续非空用户原文:{location}")
    if fragment not in user_message:
        _invalid_response(f"evidence不是连续非空用户原文:{location}")


def _is_ordered_subset(
    subset: list[Any],
    superset: list[Any],
) -> bool:
    """subset 的元素是否按原相对顺序全部出现在 superset 中。"""

    iterator = iter(superset)
    return all(item in iterator for item in subset)


def _validate_optional_positive_integer(value: object, location: str) -> None:
    if value is None:
        return
    if type(value) is not int or value <= 0:
        _invalid_response(f"{location}必须是正整数或null")


def _validate_string_array(value: object, location: str) -> None:
    if not isinstance(value, list):
        _invalid_response(f"{location}必须是数组")
    if any(not isinstance(item, str) for item in value):
        _invalid_response(f"{location}的元素必须是字符串")
    _require_no_duplicates(value, location)


def _require_allowed_values(
    values: Iterable[str],
    allowed_values: Collection[str],
    location: str,
) -> None:
    if any(value not in allowed_values for value in values):
        _invalid_response(f"{location}包含非法值")


def _require_no_duplicates(values: list[Any], location: str) -> None:
    canonical_values = [
        json.dumps(value, ensure_ascii=False, sort_keys=True)
        for value in values
    ]
    if len(canonical_values) != len(set(canonical_values)):
        _invalid_response(f"{location}不允许重复值")


def _require_exact_fields(
    value: Mapping[str, Any],
    expected_fields: Collection[str],
    location: str,
) -> None:
    if set(value) != set(expected_fields):
        _invalid_response(f"{location}字段必须与Schema完全一致")


def _invalid_response(message: str) -> None:
    raise MultiTurnConstraintError(502, message)


def _build_prompt(
    session_id: int,
    user_message: str,
    previous: dict[str, Any] | None,
    ingredient_categories: set[str],
) -> str:
    allowed_values = {
        "meal_periods": sorted(MEAL_PERIODS),
        "dish_type": sorted(DISH_TYPES),
        "taste_preferences.keys": sorted(TASTE_PREFERENCES),
        "cuisines": sorted(CUISINES),
        "effects": sorted(EFFECTS),
        "special_populations": sorted(SPECIAL_POPULATIONS),
        "required_ingredients.kind": sorted(INGREDIENT_REQUIREMENT_KINDS),
        "category": sorted(ingredient_categories),
        "concept": sorted(INGREDIENT_CONCEPTS),
        "max_difficulty": ["简单", "中等"],
        "change_actions.action": ["add", "replace", "remove"],
    }
    state_text = (
        json.dumps(previous, ensure_ascii=False, separators=(",", ":"))
        if previous is not None
        else "尚无约束(首轮)"
    )

    sections = [
        (
            "你负责从多轮中文对话中提取菜单约束。每轮你会收到当前轮用户原文"
            "和已有约束状态,需要结合两者判断本轮对约束的新增、修改与删除,"
            "并通过工具调用返回完整更新后的约束。字段类型与必填项以工具参数"
            "定义为准,全部数字字段输出 JSON 数字(不带引号),未明确时为 JSON "
            "null。任何可空字段都绝对不得用空字符串\"\"代替 null。"
        ),
        "字段允许值:\n"
        + json.dumps(allowed_values, ensure_ascii=False, indent=2),
        (
            "归一规则:微辣、香辣、麻辣归一为is_spicy=true;不辣归一为"
            "is_spicy=false;咸鲜归一为is_salty=true。暖胃、胃口不好、养胃、"
            "健胃消食、便秘归一为养胃健胃消食;夜宵归一为晚餐;公司、上班、"
            "下班归一为上班族;仪式感、稍微正式点、正式一点归一为西餐风味;"
            "清爽、别太抢味归一为is_light=true。补气血归一为贫血;减脂保留为"
            "减脂;别太甜归一为is_sweet=false。面保留为kind=concept、value=面,"
            "不提前展开。原文提到一桌菜、主菜、几个菜等菜品分类说法时,"
            "dish_type必须填菜,不得使用未指定;只有完全没有菜品分类线索时才"
            "使用未指定。家常一点、家常菜、简单、简单点归一为"
            "max_difficulty=简单;别整得太难做、别太难做、别太复杂、太麻烦不行、"
            "太麻烦的不行"
            "归一为max_difficulty=中等;难度不限、麻烦点也行、复杂点也能接受"
            "解除难度限制并置null;复杂单独出现时忽略。适合夏天、热乎、牙口"
            "不好等没有既定映射的描述一律忽略,不得填入任何字段。周末、平时等"
            "没有既定映射的时间表达一律忽略,不填入meal_periods。"
            "available_ingredients只保存可用核心食材;盐、油、水等辅料无需列入,"
            "也不表示列出的食材必须全部使用。共用食材、不想分开做两套属于"
            "跨组组合优化,直接忽略。没有既定映射的描述直接忽略。"
        ),
        (
            "食材名规则:ingredient 的值使用常见标准名称,如番茄、鸡蛋、土豆、"
            "猪肉、牛肉、鸡肉、鱼、虾、白菜、豆腐、米饭、面条;用户用了同义说法"
            "(如西红柿、马铃薯)时归一到标准名称;无法确定时输出用户原文说法。"
        ),
        (
            "菜品规则:dishes至少包含一项。没有明确菜品分类时返回一项"
            "count=null、dish_type=未指定且其余约束为空的菜品,并将口味、菜系、"
            "功效、人群和必需食材直接放入该项;存在多个菜品组时,适用于所有组的"
            "限制复制到每个Dish中。total_dish_count表示整桌确切菜品总数;"
            "len(dishes)表示查询组数;Dish.count只表示用户明确分配给该组的"
            "菜品数,三者不得混用。四个菜写total_dish_count=4,默认Dish.count"
            "仍为null。一人要求、另一人拒绝同一布尔口味时拆成两个真实Dish,"
            "分别保存true和false,两个count都为null;一个人不是菜品数量证据。"
        ),
        (
            "演化规则:标量(diner_count、total_dish_count、"
            "max_total_time_minutes):增=旧值累加"
            "(再加一个人 2→3);改=新值覆盖(改成三个人);删=解除约束置null"
            "(人数不限)。数组(meal_periods、available_ingredients及Dish内"
            "cuisines、effects、special_populations、required_ingredients):"
            "增=追加元素去重保序;删=移除元素;改=整体替换。口味"
            "(taste_preferences):增=新增键;改=同名键新值覆盖(改口);"
            "删=移除键。max_difficulty只允许replace和remove,add非法。"
            "dishes:增=同类型count累加或新增菜品组;"
            "删=移除整个Dish;改=替换count或修改Dish内字段。"
            "上一状态中已有的约束,只要本轮原文没有改变它们的表述,必须"
            "原样保留,不得修改或删除。"
        ),
        (
            "变更声明规则:每轮对上一状态做的每个增删改都必须在change_actions"
            "中声明。作用于顶层字段时填field(meal_periods、diner_count、"
            "total_dish_count、max_total_time_minutes、max_difficulty、"
            "available_ingredients);作用于Dish时填"
            "dish_index(上一状态中的Dish索引);新增全新菜品组时dish_index为"
            "null且放在输出dishes末尾。field与dish_index必须恰好一个非空,"
            "唯一例外是新增全新菜品组(action=add)时两者均为null;"
            "同一字段或同一Dish只允许一条声明;未声明的字段和Dish必须原样保留。"
            "标量或Dish的count在旧值为null时必须用replace;每条声明的evidence"
            "必须是本轮原文的连续片段。add只用于在旧值基础上继续增加"
            "(如再加一个人 2→3);旧值为null、或本轮给出的是新的明确数值"
            "(如两个人、别超过45分钟)时,一律用replace。当前约束状态为"
            "尚无约束(首轮)时,change_actions必须输出[],所有约束直接写入输出字段。"
            "输出前自检:①每条声明引用的dish_index必须存在于上一状态;"
            "②action为add且dish_index为null时,输出dishes末尾必须比上一状态"
            "恰好多一项;③未声明的字段与Dish必须与上一状态完全一致,"
            "声明与输出之间不得有对不上的地方。"
            "已有明确总数且未指定菜品组时,再加一个菜只增加"
            "total_dish_count,不修改任何Dish.count。指定组count和总数都明确"
            "时,该组再加一道必须分别声明并同时增加total_dish_count和组count。"
            "输出前逐项检查所有可空数值字段:上一状态为null且本轮未改变时"
            "必须继续输出JSON null,不得输出空字符串。明确对照:"
            "错误写法为\"total_dish_count\":\"\";正确写法为"
            "\"total_dish_count\":null。"
        ),
        (
            "证据规则:只为本轮新增或变更的字段提供evidence,使用叶子路径"
            "(如meal_periods[0]、diner_count、total_dish_count、"
            "max_difficulty、dishes[0].count、"
            "dishes[0].taste_preferences.is_spicy、"
            "dishes[0].required_ingredients[0].value),片段必须是本轮原文的"
            "连续子串;上一状态已有的字段不要重复提供evidence。首轮所有非空"
            "约束都必须提供evidence。dialogue_id、null、[]、{}和默认未指定"
            "Dish不需要证据。"
        ),
        (
            "当前对话绑定规则:输出 dialogue_id 必须原样复制当前会话id,"
            f"本次必须输出 dialogue_id={session_id},不得使用其他值。"
        ),
        "参考示例(必须在上述示例之后处理当前对话):\n"
        + "\n\n".join(
            "当前约束状态:"
            + (
                json.dumps(state, ensure_ascii=False, separators=(",", ":"))
                if state is not None
                else "尚无约束(首轮)"
            )
            + "\n当前对话原文:"
            + message
            + "\n对应输出:"
            + json.dumps(result, ensure_ascii=False, separators=(",", ":"))
            for state, message, result in _build_multi_turn_examples()
        ),
        "当前约束状态:\n" + state_text,
        "当前对话原文:\n" + user_message,
    ]
    return "\n\n".join(sections)


_EMPTY_DISH_EXAMPLE = {
    "count": None,
    "dish_type": "未指定",
    "taste_preferences": {},
    "cuisines": [],
    "effects": [],
    "special_populations": [],
    "required_ingredients": [],
}


def _example_dish(**overrides: Any) -> dict[str, Any]:
    """构造示例用的空 Dish 并覆盖指定字段。"""

    dish = dict(_EMPTY_DISH_EXAMPLE)
    dish.update(overrides)
    return dish


def _build_multi_turn_examples(
) -> list[tuple[dict[str, Any] | None, str, dict[str, Any]]]:
    """多轮提取的参考示例:每项为(上一状态,本轮原文,期望输出)。"""

    example_1_result = {
        "dialogue_id": 9001,
        "meal_periods": ["晚餐"],
        "diner_count": 2,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [_example_dish()],
        "evidence": {
            "meal_periods[0]": "晚饭",
            "diner_count": "两个人",
        },
        "change_actions": [],
    }
    example_1_state = {
        "dialogue_id": 9001,
        "meal_periods": ["晚餐"],
        "diner_count": 2,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [_example_dish()],
        "evidence": {
            "meal_periods[0]": "晚饭",
            "diner_count": "两个人",
        },
    }
    example_2_result = {
        "dialogue_id": 9001,
        "meal_periods": ["晚餐"],
        "diner_count": None,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [
            _example_dish(
                taste_preferences={
                    "is_spicy": False,
                    "is_light": True,
                },
            ),
        ],
        "evidence": {
            "dishes[0].taste_preferences.is_spicy": "辣的",
            "dishes[0].taste_preferences.is_light": "清淡",
        },
        "change_actions": [
            {
                "field": None,
                "dish_index": 0,
                "action": "replace",
                "evidence": "别做辣的",
            }
        ],
    }
    example_3_state = {
        "dialogue_id": 9002,
        "meal_periods": ["晚餐"],
        "diner_count": 2,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [
            _example_dish(count=2, dish_type="菜"),
            _example_dish(count=1, dish_type="汤"),
        ],
        "evidence": {
            "meal_periods[0]": "晚上",
            "diner_count": "两个人",
            "dishes[0].count": "两菜",
            "dishes[0].dish_type": "两菜",
            "dishes[1].count": "一汤",
            "dishes[1].dish_type": "一汤",
        },
    }
    example_3_result = {
        "dialogue_id": 9002,
        "meal_periods": ["晚餐"],
        "diner_count": 2,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [
            _example_dish(count=3, dish_type="菜"),
            _example_dish(count=1, dish_type="汤"),
        ],
        "evidence": {"dishes[0].count": "再加一个菜"},
        "change_actions": [
            {
                "field": None,
                "dish_index": 0,
                "action": "add",
                "evidence": "再加一个菜",
            }
        ],
    }
    example_4_state = {
        "dialogue_id": 9003,
        "meal_periods": ["午餐"],
        "diner_count": None,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [_example_dish()],
        "evidence": {"meal_periods[0]": "中午"},
    }
    example_4_result = {
        "dialogue_id": 9003,
        "meal_periods": ["午餐"],
        "diner_count": 2,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [_example_dish(effects=["减脂"])],
        "evidence": {
            "diner_count": "两个人",
            "dishes[0].effects[0]": "减脂",
        },
        "change_actions": [
            {
                "field": "diner_count",
                "dish_index": None,
                "action": "replace",
                "evidence": "两个人",
            },
            {
                "field": None,
                "dish_index": 0,
                "action": "replace",
                "evidence": "减脂",
            },
        ],
    }
    example_5_state = {
        "dialogue_id": 9004,
        "meal_periods": ["晚餐"],
        "diner_count": 2,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [_example_dish()],
        "evidence": {
            "meal_periods[0]": "晚饭",
            "diner_count": "两个人",
        },
    }
    example_5_result = {
        "dialogue_id": 9004,
        "meal_periods": ["晚餐"],
        "diner_count": 2,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [
            _example_dish(
                taste_preferences={"is_spicy": True},
            ),
            _example_dish(
                taste_preferences={"is_spicy": False},
            ),
        ],
        "evidence": {
            "dishes[0].taste_preferences.is_spicy": "一个人想吃辣",
            "dishes[1].taste_preferences.is_spicy": "一点辣都不想碰",
        },
        "change_actions": [
            {
                "field": None,
                "dish_index": 0,
                "action": "replace",
                "evidence": "一个人想吃辣",
            },
            {
                "field": None,
                "dish_index": None,
                "action": "add",
                "evidence": "一点辣都不想碰",
            },
        ],
    }
    example_6_result = {
        "dialogue_id": 9005,
        "meal_periods": [],
        "diner_count": None,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [_example_dish(dish_type="菜")],
        "evidence": {"dishes[0].dish_type": "一桌菜"},
        "change_actions": [],
    }
    example_6_state = {
        key: value
        for key, value in example_6_result.items()
        if key != "change_actions"
    }
    example_7_state = {
        "dialogue_id": 9006,
        "meal_periods": ["晚餐"],
        "diner_count": 2,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [
            _example_dish(
                taste_preferences={"is_spicy": True},
            ),
            _example_dish(
                taste_preferences={"is_spicy": False},
            ),
        ],
        "evidence": {
            "meal_periods[0]": "晚饭",
            "diner_count": "两个人",
            "dishes[0].taste_preferences.is_spicy": "一个人想吃辣",
            "dishes[1].taste_preferences.is_spicy": "一点辣都不想碰",
        },
    }
    example_7_result = {
        "dialogue_id": 9006,
        "meal_periods": ["晚餐"],
        "diner_count": 2,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [
            _example_dish(
                dish_type="菜",
                taste_preferences={"is_spicy": True},
                required_ingredients=[
                    {"kind": "ingredient", "value": "鱼"},
                    {"kind": "ingredient", "value": "鸡翅"},
                ],
            ),
            _example_dish(
                taste_preferences={"is_spicy": False},
            ),
        ],
        "evidence": {
            "dishes[0].dish_type": "主菜",
            "dishes[0].required_ingredients[0].value": "鱼",
            "dishes[0].required_ingredients[1].value": "鸡翅",
        },
        "change_actions": [
            {
                "field": None,
                "dish_index": 0,
                "action": "replace",
                "evidence": "主菜",
            }
        ],
    }
    example_8_state = {
        "dialogue_id": 9007,
        "meal_periods": ["晚餐"],
        "diner_count": None,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [_example_dish(effects=["贫血"])],
        "evidence": {
            "meal_periods[0]": "晚饭",
            "dishes[0].effects[0]": "补气血",
        },
    }
    example_8_result = {
        "dialogue_id": 9007,
        "meal_periods": ["晚餐"],
        "diner_count": None,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [_example_dish(effects=["贫血"])],
        "evidence": {"max_difficulty": "家常一点"},
        "change_actions": [
            {
                "field": "max_difficulty",
                "dish_index": None,
                "action": "replace",
                "evidence": "家常一点",
            }
        ],
        "max_difficulty": "简单",
    }

    example_9_state = {
        "dialogue_id": 9008,
        "meal_periods": ["晚餐"],
        "diner_count": None,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [_example_dish()],
        "evidence": {"meal_periods[0]": "晚饭"},
    }
    example_9_result = {
        **example_9_state,
        "max_total_time_minutes": 45,
        "evidence": {
            "max_total_time_minutes": "整体别超过45分钟",
            "max_difficulty": "太麻烦的不行",
        },
        "max_difficulty": "中等",
        "change_actions": [
            {
                "field": "max_total_time_minutes",
                "dish_index": None,
                "action": "replace",
                "evidence": "整体别超过45分钟",
            },
            {
                "field": "max_difficulty",
                "dish_index": None,
                "action": "replace",
                "evidence": "太麻烦的不行",
            }
        ],
    }
    example_10_result = {
        "dialogue_id": 9009,
        "meal_periods": [],
        "diner_count": None,
        "total_dish_count": 4,
        "max_total_time_minutes": None,
        "max_difficulty": None,
        "available_ingredients": [],
        "dishes": [_example_dish()],
        "evidence": {"total_dish_count": "四道菜"},
        "change_actions": [],
    }
    example_10_state = {
        key: value
        for key, value in example_10_result.items()
        if key != "change_actions"
    }
    example_11_result = {
        **example_10_state,
        "dishes": [
            _example_dish(taste_preferences={"is_spicy": True}),
            _example_dish(taste_preferences={"is_spicy": False}),
        ],
        "evidence": {
            "dishes[0].taste_preferences.is_spicy": "一个人吃辣",
            "dishes[1].taste_preferences.is_spicy": "一个人不碰辣",
        },
        "change_actions": [
            {
                "field": None,
                "dish_index": 0,
                "action": "replace",
                "evidence": "一个人吃辣",
            },
            {
                "field": None,
                "dish_index": None,
                "action": "add",
                "evidence": "一个人不碰辣",
            },
        ],
    }
    example_11_state = {
        **example_11_result,
        "evidence": {
            "total_dish_count": "四道菜",
            "dishes[0].taste_preferences.is_spicy": "一个人吃辣",
            "dishes[1].taste_preferences.is_spicy": "一个人不碰辣",
        },
    }
    example_11_state.pop("change_actions")
    example_12_result = {
        **example_11_state,
        "total_dish_count": 5,
        "evidence": {"total_dish_count": "再加一道"},
        "change_actions": [
            {
                "field": "total_dish_count",
                "dish_index": None,
                "action": "add",
                "evidence": "再加一道",
            }
        ],
    }
    example_13_result = {
        **example_6_state,
        "diner_count": 6,
        "max_difficulty": "中等",
        "dishes": [
            _example_dish(dish_type="菜", cuisines=["西餐风味"]),
        ],
        "evidence": {
            "diner_count": "大概六个人",
            "max_difficulty": "别整得太难做",
            "dishes[0].cuisines[0]": "稍微正式点",
        },
        "change_actions": [
            {
                "field": "diner_count",
                "dish_index": None,
                "action": "replace",
                "evidence": "大概六个人",
            },
            {
                "field": "max_difficulty",
                "dish_index": None,
                "action": "replace",
                "evidence": "别整得太难做",
            },
            {
                "field": None,
                "dish_index": 0,
                "action": "replace",
                "evidence": "稍微正式点",
            },
        ],
    }

    examples = [
        (None, "帮我想一顿两个人的晚饭。", example_1_result),
        (None, "周末想请几个人来家里吃饭，你帮我设计一桌菜。", example_6_result),
        (example_1_state, "别做辣的，口味清淡一点。", example_2_result),
        (example_3_state, "再加一个菜", example_3_result),
        (example_4_state, "两个人吃，最近在减脂。", example_4_result),
        (example_5_state, "一个人想吃辣，一个人一点辣都不想碰。", example_5_result),
        (
            example_7_state,
            "最好大部分食材能共用，主菜可以考虑鱼或者鸡翅。",
            example_7_result,
        ),
        (example_8_state, "家常一点。", example_8_result),
        (
            example_9_state,
            "然后整体别超过45分钟，太麻烦的不行。",
            example_9_result,
        ),
        (None, "四道菜。", example_10_result),
        (
            example_10_state,
            "一个人吃辣，一个人不碰辣。",
            example_11_result,
        ),
        (example_11_state, "再加一道。", example_12_result),
        (
            example_6_state,
            "大概六个人，稍微正式点，但别整得太难做。",
            example_13_result,
        ),
    ]
    for state, _, result in examples:
        result.setdefault("total_dish_count", None)
        result.setdefault("max_difficulty", None)
        if state is not None:
            state.setdefault("total_dish_count", None)
            state.setdefault("max_difficulty", None)
    return examples


__all__ = [
    "MultiTurnConstraintError",
    "MultiTurnConstraintService",
]
