from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Collection, Iterable, Mapping
from typing import Any

from sqlalchemy.orm import Session

from backend.core.dialogue_constraint_contract import (
    CHANGEABLE_TOP_FIELDS,
    CHANGE_ACTION_FIELDS,
    CHANGE_ACTIONS,
    CUISINES,
    DISH_FIELDS,
    DISH_TYPES,
    DialogueConstraintExtractionError,
    EFFECTS,
    INGREDIENT_CONCEPTS,
    INGREDIENT_GROUP_FIELDS,
    INGREDIENT_GROUP_MATCHES,
    INGREDIENT_REQUIREMENT_FIELDS,
    INGREDIENT_REQUIREMENT_KINDS,
    MEAL_PERIODS,
    MERGED_CONSTRAINT_FIELDS,
    MISSING_REQUIREMENTS,
    SCALAR_FIELDS,
    SESSION_STATUSES,
    SPECIAL_POPULATIONS,
    TASTE_PREFERENCES,
    TOP_LEVEL_FIELDS,
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

from .dialogue_constraint_prompt import (
    build_dialogue_prompt,
    build_retry_prompt,
)


SessionFactory = Callable[[], Session]

# 状态与缺失要素的具名常量,取自契约枚举,避免魔法字符串
_, NEEDS_CONFIRMATION, READY_FOR_PLANNING = SESSION_STATUSES
MISSING_DINER, MISSING_DISH_TYPE = MISSING_REQUIREMENTS


class DialogueConstraintService:
    """管理统一约束会话：状态落库、LLM合并与完整性判定。"""

    def __init__(
        self,
        session_factory: SessionFactory,
        llm_client: Callable[[str], object],
        meal_period_service: object,
    ) -> None:
        if not callable(session_factory):
            raise DialogueConstraintExtractionError(500, "Session工厂无效")
        if not callable(llm_client):
            raise DialogueConstraintExtractionError(500, "LLM约束提取器无效")
        if meal_period_service is None or not callable(
            getattr(meal_period_service, "resolve", None)
        ):
            raise DialogueConstraintExtractionError(500, "餐次解析服务无效")
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
                raise DialogueConstraintExtractionError(500, str(exc)) from exc
            if profile is None:
                raise DialogueConstraintExtractionError(409, "用户档案不存在")
            try:
                session_id = insert_dialogue_session(
                    session,
                    validated_profile_id,
                )
                session.commit()
                return session_id
            except DialogueStateRepositoryError as exc:
                raise DialogueConstraintExtractionError(500, str(exc)) from exc

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
            raise DialogueConstraintExtractionError(
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
            except DialogueConstraintExtractionError:
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
                raise DialogueConstraintExtractionError(400, "会话不存在")
            return _build_state(row)

    def _open_session(self) -> Session:
        try:
            session = self._session_factory()
        except Exception as exc:
            raise DialogueConstraintExtractionError(
                500,
                "数据库 Session 创建失败",
            ) from exc
        if not isinstance(session, Session):
            raise DialogueConstraintExtractionError(500, "数据库 Session 无效")
        return session

    def _load_session_row(self, session: Session, session_id: int):
        try:
            return load_dialogue_session(session, session_id)
        except DialogueStateRepositoryError as exc:
            raise DialogueConstraintExtractionError(500, str(exc)) from exc

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
            raise DialogueConstraintExtractionError(500, str(exc)) from exc
        if session_row is None:
            raise DialogueConstraintExtractionError(400, "会话不存在")

        try:
            turn_number = next_turn_number(session, session_id)
            ingredient_names, ingredient_categories = (
                load_ingredient_constraint_values(session)
            )
        except (DialogueStateRepositoryError, IngredientRepositoryError) as exc:
            raise DialogueConstraintExtractionError(500, str(exc)) from exc

        previous = session_row.merged_constraints
        prompt = build_dialogue_prompt(
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
        except DialogueConstraintExtractionError as exc:
            if exc.status_code != 502:
                raise
            # 将首次具体错误反馈给模型后只重试一次；再次违例仍按502抛出
            retry_prompt = build_retry_prompt(prompt, str(exc))
            merged = _extract_and_merge(
                retry_prompt,
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
            raise DialogueConstraintExtractionError(500, str(exc)) from exc

        return {
            "session_id": session_id,
            "turn_number": turn_number,
            "status": status,
            "merged_constraints": merged,
            "missing_requirements": missing,
        }


def _validate_positive_integer(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise DialogueConstraintExtractionError(400, f"{name}必须是正整数")
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
        raise DialogueConstraintExtractionError(500, str(exc)) from exc

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
        raise DialogueConstraintExtractionError(
            503,
            "LLM服务请求超时或不可用",
        ) from exc

    if not isinstance(result, dict):
        raise DialogueConstraintExtractionError(502, "LLM必须返回结构化对象")
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
    """保持结构化输出原值，由严格类型校验拒绝字符串数字。"""

    return result


def _validate_turn_output(
    result: dict[str, Any],
    session_id: int,
    ingredient_names: set[str],
    ingredient_categories: set[str],
) -> dict[str, Any]:
    _require_exact_fields(result, TOP_LEVEL_FIELDS, "顶层")

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

    groups = dish["required_ingredient_groups"]
    if not isinstance(groups, list):
        _invalid_response(
            f"{location}.required_ingredient_groups必须是数组"
        )
    _require_no_duplicates(groups, f"{location}.required_ingredient_groups")
    seen_requirements: set[tuple[str, str]] = set()
    for group_index, group in enumerate(groups):
        _validate_ingredient_group(
            group,
            f"{location}.required_ingredient_groups[{group_index}]",
            ingredient_names,
            ingredient_categories,
            seen_requirements,
        )


def _validate_ingredient_group(
    group: object,
    location: str,
    ingredient_names: set[str],
    ingredient_categories: set[str],
    seen_requirements: set[tuple[str, str]],
) -> None:
    if not isinstance(group, dict):
        _invalid_response(f"{location}必须是对象")
    _require_exact_fields(group, INGREDIENT_GROUP_FIELDS, location)
    match = group["match"]
    items = group["items"]
    if match not in INGREDIENT_GROUP_MATCHES:
        _invalid_response(f"{location}.match只允许all或any")
    if not isinstance(items, list) or not items:
        _invalid_response(f"{location}.items至少包含一项")
    if match == "any" and len(items) < 2:
        _invalid_response(f"{location}.items在any组中至少包含两项")
    _require_no_duplicates(items, f"{location}.items")
    for requirement_index, requirement in enumerate(items):
        _validate_ingredient_requirement(
            requirement,
            f"{location}.items[{requirement_index}]",
            ingredient_names,
            ingredient_categories,
        )
        key = (requirement["kind"], requirement["value"])
        if key in seen_requirements:
            _invalid_response(f"{location}.items包含跨组重复食材条件")
        seen_requirements.add(key)


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
        key: output[key] for key in MERGED_CONSTRAINT_FIELDS
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
            _apply_top_field_action(replayed, output, field, kind)
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


def _apply_top_field_action(
    replayed: dict[str, Any],
    output: dict[str, Any],
    field: str,
    kind: str,
) -> None:
    """校验并重放一条顶层字段变更。"""

    old_value = replayed[field]
    new_value = output[field]
    if field == "max_difficulty":
        _validate_difficulty_action(kind, new_value)
    elif field in SCALAR_FIELDS:
        _validate_scalar_action(field, kind, old_value, new_value)
    else:
        _validate_array_action(field, kind, old_value, new_value)
    replayed[field] = copy.deepcopy(new_value)


def _validate_difficulty_action(kind: str, new_value: object) -> None:
    if kind == "add":
        _invalid_response("max_difficulty不允许add")
    if kind == "remove" and new_value is not None:
        _invalid_response("max_difficulty remove要求输出为null")


def _validate_scalar_action(
    field: str,
    kind: str,
    old_value: int | None,
    new_value: int | None,
) -> None:
    if kind == "add" and (
        old_value is None
        or new_value is None
        or new_value <= old_value
    ):
        _invalid_response(f"标量add要求旧值非空且新值大于旧值:{field}")
    if kind == "remove" and new_value is not None:
        _invalid_response(f"标量remove要求输出为null:{field}")


def _validate_array_action(
    field: str,
    kind: str,
    old_value: list[Any],
    new_value: list[Any],
) -> None:
    if kind == "add" and not _is_ordered_subset(old_value, new_value):
        _invalid_response(f"数组add要求输出包含旧数组全部元素:{field}")
    if kind == "remove" and not _is_ordered_subset(new_value, old_value):
        _invalid_response(f"数组remove要求输出是旧数组的子集:{field}")


def _constraints_equal(
    replayed: dict[str, Any],
    output: dict[str, Any],
) -> bool:
    for key in MERGED_CONSTRAINT_FIELDS:
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
        key: output[key] for key in MERGED_CONSTRAINT_FIELDS
    }
    previous_paths = _collect_leaf_paths(previous)
    for path in _collect_leaf_paths(output_constraints):
        inherited = _find_inherited_evidence(
            previous,
            output,
            path,
            previous_paths,
        )
        if inherited is not None:
            merged[path] = inherited
        else:
            fragment = output["evidence"].get(path)
            _require_evidence_fragment(fragment, user_message, path)
            merged[path] = fragment
    return merged


def _find_inherited_evidence(
    previous: Mapping[str, Any],
    output: Mapping[str, Any],
    path: str,
    previous_paths: set[str],
) -> str | None:
    """按相同路径或唯一同形值继承因数组移位而变化的证据路径。"""

    output_value = _value_at(output, path)
    if path in previous_paths and _value_at(previous, path) == output_value:
        return previous["evidence"][path]

    path_shape = re.sub(r"\[\d+\]", "[]", path)
    candidates = [
        previous_path
        for previous_path in previous_paths
        if re.sub(r"\[\d+\]", "[]", previous_path) == path_shape
        and _value_at(previous, previous_path) == output_value
    ]
    if len(candidates) == 1:
        return previous["evidence"][candidates[0]]
    return None


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
        for group_index, group in enumerate(
            dish["required_ingredient_groups"]
        ):
            group_prefix = (
                f"{prefix}.required_ingredient_groups[{group_index}]"
            )
            paths.add(f"{group_prefix}.match")
            paths.update(
                f"{group_prefix}.items[{item_index}].value"
                for item_index in range(len(group["items"]))
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
    raise DialogueConstraintExtractionError(502, message)


__all__ = [
    "DialogueConstraintExtractionError",
    "DialogueConstraintService",
]
