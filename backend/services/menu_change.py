from __future__ import annotations

import copy
from collections.abc import Collection, Mapping
from math import ceil
from typing import Any, Literal, TypedDict


MENU_CHANGE_MODES = (
    "none",
    "replace_all",
    "replace_partial",
    "replace_specific",
    "restore_specific",
)
MENU_CHANGE_FIELDS = (
    "mode",
    "replace_count",
    "target_positions",
    "target_recipe_names",
    "replacement_recipe_name",
    "unresolved_target",
    "evidence",
)


class MenuSnapshotItem(TypedDict):
    position: int
    dish_constraint_index: int
    recipe_name: str


class MenuChangePolicy(TypedDict):
    previous_recipe_names: list[str]
    required_previous_count: int | None
    required_recipe_names: list[str]
    forbidden_recipe_names: list[str]


class MenuChangeError(ValueError):
    """换菜状态或意图结构非法。"""


def resolve_menu_change(
    last_menu: object,
    excluded_recipe_names: object,
    menu_change: object,
    recommendable_recipe_names: Collection[str],
) -> dict[str, Any]:
    """把本轮换菜意图解析为菜单求解硬约束。"""

    change = validate_menu_change(menu_change)
    excluded = _validate_recipe_names(
        excluded_recipe_names,
        "excluded_recipe_names",
    )
    if not isinstance(recommendable_recipe_names, Collection) or isinstance(
        recommendable_recipe_names,
        (str, bytes),
    ):
        raise MenuChangeError("recommendable_recipe_names必须是菜名集合")
    recommendable = {
        name
        for name in recommendable_recipe_names
        if isinstance(name, str) and name
    }

    if change["mode"] == "none":
        return _ready_result(None, 0)

    menu = _validate_last_menu(last_menu)
    if not menu:
        return _confirmation_result(
            change,
            "当前还没有可调整的历史菜单，请先生成一份菜单。",
        )
    if change["unresolved_target"] is not None:
        return _confirmation_result(change, change["unresolved_target"])

    previous_names = [item["recipe_name"] for item in menu]
    resolved_targets = _resolve_target_names(change, menu)
    if resolved_targets is None:
        return _confirmation_result(
            change,
            "指定的菜名不存在或菜单序号越界，请重新确认换菜目标。",
        )
    target_names = resolved_targets
    mode = change["mode"]
    restored_name: str | None = None

    if mode == "replace_all":
        target_names = list(previous_names)
        replace_count = len(previous_names)
        required_previous_count = 0
        required_names: list[str] = []
    elif mode == "replace_partial":
        raw_count = change["replace_count"]
        replace_count = ceil(len(previous_names) / 2) if raw_count is None else raw_count
        if not 1 <= replace_count <= len(previous_names):
            return _confirmation_result(
                change,
                f"换菜数量必须在1到{len(previous_names)}道之间。",
            )
        required_previous_count = len(previous_names) - replace_count
        required_names = []
    elif mode == "replace_specific":
        if not target_names:
            return _confirmation_result(change, "请说明要替换的菜名或菜单序号。")
        replace_count = len(target_names)
        required_previous_count = len(previous_names) - replace_count
        required_names = [
            name for name in previous_names if name not in set(target_names)
        ]
    else:
        restored_name = change["replacement_recipe_name"]
        if restored_name is None:
            return _confirmation_result(change, "请说明要换回的菜名。")
        if restored_name not in excluded:
            return _confirmation_result(
                change,
                f"{restored_name}不是本会话中已换出的菜。",
            )
        if restored_name not in recommendable:
            return _confirmation_result(
                change,
                f"{restored_name}不在正式可推荐菜谱库中。",
            )
        replace_count = max(1, len(target_names))
        required_previous_count = len(previous_names) - replace_count
        required_names = (
            [
                name
                for name in previous_names
                if name not in set(target_names)
            ]
            if target_names
            else []
        )
        required_names.append(restored_name)

    replacement_name = change["replacement_recipe_name"]
    if mode != "restore_specific" and replacement_name is not None:
        if replacement_name not in recommendable:
            return _confirmation_result(
                change,
                f"{replacement_name}不在正式可推荐菜谱库中。",
            )
        if replacement_name in excluded:
            return _confirmation_result(
                change,
                f"{replacement_name}已在本会话排除，请明确说换回来。",
            )
        if replacement_name in previous_names:
            return _confirmation_result(
                change,
                f"{replacement_name}已经在当前菜单中。",
            )
        required_names.append(replacement_name)

    effective_excluded = [
        name for name in excluded if name != restored_name
    ]
    forbidden_names = list(effective_excluded)
    if mode in {"replace_all", "replace_specific", "restore_specific"}:
        forbidden_names.extend(target_names)
    forbidden_names = _ordered_unique(forbidden_names)
    required_names = _ordered_unique(required_names)
    if set(required_names) & set(forbidden_names):
        return _confirmation_result(change, "换入菜与必须换出菜发生冲突。")

    policy: MenuChangePolicy = {
        "previous_recipe_names": previous_names,
        "required_previous_count": required_previous_count,
        "required_recipe_names": required_names,
        "forbidden_recipe_names": forbidden_names,
    }
    return _ready_result(policy, replace_count)


def filter_excluded_candidates(
    dish_candidates: object,
    excluded_recipe_names: object,
) -> list[list[dict[str, Any]]]:
    """从所有菜品候选组中剔除会话排除菜。"""

    if not isinstance(dish_candidates, list) or any(
        not isinstance(group, list) for group in dish_candidates
    ):
        raise MenuChangeError("dish_candidates必须是候选组数组")
    excluded = set(
        _validate_recipe_names(
            excluded_recipe_names,
            "excluded_recipe_names",
        )
    )
    filtered: list[list[dict[str, Any]]] = []
    for group in dish_candidates:
        result_group: list[dict[str, Any]] = []
        for candidate in group:
            if not isinstance(candidate, Mapping):
                raise MenuChangeError("候选菜必须是对象")
            name = candidate.get("recipe_name")
            if not isinstance(name, str) or not name:
                raise MenuChangeError("候选菜缺少有效recipe_name")
            if name not in excluded:
                result_group.append(copy.deepcopy(dict(candidate)))
        filtered.append(result_group)
    return filtered


def finalize_menu_state(
    last_menu: object,
    excluded_recipe_names: object,
    menu_change: object,
    status: str,
    selected_dishes: object,
) -> dict[str, Any]:
    """按规划终态结算最新菜单与会话排除集合。"""

    old_menu = _validate_last_menu(last_menu)
    excluded = _validate_recipe_names(
        excluded_recipe_names,
        "excluded_recipe_names",
    )
    change = validate_menu_change(menu_change)
    if status != "recommended":
        return {
            "last_menu": copy.deepcopy(old_menu) if old_menu else None,
            "excluded_recipe_names": list(excluded),
            "pending_menu_change": None,
            "menu_change_result": None,
        }

    new_menu = _validate_last_menu(selected_dishes)
    if not new_menu:
        raise MenuChangeError("recommended状态必须包含新菜单")
    normalized_menu = [
        {
            **copy.deepcopy(item),
            "position": index,
        }
        for index, item in enumerate(new_menu, start=1)
    ]
    if change["mode"] == "none":
        return {
            "last_menu": normalized_menu,
            "excluded_recipe_names": list(excluded),
            "pending_menu_change": None,
            "menu_change_result": None,
        }

    old_names = [item["recipe_name"] for item in old_menu]
    new_names = [item["recipe_name"] for item in normalized_menu]
    removed = [name for name in old_names if name not in set(new_names)]
    added = [name for name in new_names if name not in set(old_names)]
    retained = [name for name in new_names if name in set(old_names)]
    updated_excluded = list(excluded)
    if change["mode"] == "restore_specific":
        restored = change["replacement_recipe_name"]
        updated_excluded = [
            name for name in updated_excluded if name != restored
        ]
    updated_excluded = _ordered_unique(updated_excluded + removed)
    return {
        "last_menu": normalized_menu,
        "excluded_recipe_names": updated_excluded,
        "pending_menu_change": None,
        "menu_change_result": {
            "mode": change["mode"],
            "retained_recipe_names": retained,
            "removed_recipe_names": removed,
            "added_recipe_names": added,
        },
    }


def _resolve_target_names(
    change: dict[str, Any],
    menu: list[dict[str, Any]],
) -> list[str] | None:
    names = [item["recipe_name"] for item in menu]
    targets: list[str] = []
    for position in change["target_positions"]:
        if position > len(menu):
            return None
        targets.append(menu[position - 1]["recipe_name"])
    for name in change["target_recipe_names"]:
        if name not in names:
            return None
        targets.append(name)
    return _ordered_unique(targets)


def validate_menu_change(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(MENU_CHANGE_FIELDS):
        raise MenuChangeError("menu_change字段不完整或包含未知字段")
    change = copy.deepcopy(dict(value))
    mode = change["mode"]
    if mode not in MENU_CHANGE_MODES:
        raise MenuChangeError("menu_change.mode不在允许值中")
    count = change["replace_count"]
    if count is not None and (type(count) is not int or count <= 0):
        raise MenuChangeError("replace_count必须是正整数或null")
    positions = change["target_positions"]
    if not isinstance(positions, list) or any(
        type(item) is not int or item <= 0 for item in positions
    ):
        raise MenuChangeError("target_positions必须是正整数数组")
    if len(positions) != len(set(positions)):
        raise MenuChangeError("target_positions不得重复")
    change["target_recipe_names"] = _validate_recipe_names(
        change["target_recipe_names"],
        "target_recipe_names",
    )
    for field in ("replacement_recipe_name", "unresolved_target", "evidence"):
        item = change[field]
        if item is not None and (
            not isinstance(item, str) or not item.strip()
        ):
            raise MenuChangeError(f"{field}必须是非空字符串或null")
    if mode == "none" and any(
        (
            count is not None,
            bool(positions),
            bool(change["target_recipe_names"]),
            change["replacement_recipe_name"] is not None,
            change["unresolved_target"] is not None,
            change["evidence"] is not None,
        )
    ):
        raise MenuChangeError("none模式不得携带换菜参数")
    return change


def _validate_last_menu(value: object) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise MenuChangeError("last_menu必须是数组或null")
    result: list[dict[str, Any]] = []
    names: list[str] = []
    for index, raw in enumerate(value, start=1):
        if not isinstance(raw, Mapping):
            raise MenuChangeError("last_menu条目必须是对象")
        position = raw.get("position")
        dish_index = raw.get("dish_constraint_index")
        name = raw.get("recipe_name")
        if position != index:
            raise MenuChangeError("last_menu.position必须从1连续编号")
        if type(dish_index) is not int or dish_index < 0:
            raise MenuChangeError("dish_constraint_index必须是非负整数")
        if not isinstance(name, str) or not name:
            raise MenuChangeError("last_menu.recipe_name必须是非空字符串")
        names.append(name)
        result.append(copy.deepcopy(dict(raw)))
    if len(names) != len(set(names)):
        raise MenuChangeError("last_menu菜名不得重复")
    return result


def _validate_recipe_names(value: object, location: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise MenuChangeError(f"{location}必须是非空菜名数组")
    if len(value) != len(set(value)):
        raise MenuChangeError(f"{location}不得重复")
    return list(value)


def _ready_result(
    policy: MenuChangePolicy | None,
    replace_count: int,
) -> dict[str, Any]:
    return {
        "status": "ready",
        "message": None,
        "policy": policy,
        "effective_replace_count": replace_count,
        "pending_menu_change": None,
    }


def _confirmation_result(
    change: dict[str, Any],
    message: str,
) -> dict[str, Any]:
    return {
        "status": "needs_confirmation",
        "message": message,
        "policy": None,
        "effective_replace_count": None,
        "pending_menu_change": copy.deepcopy(change),
    }


def _ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


__all__ = [
    "MENU_CHANGE_FIELDS",
    "MENU_CHANGE_MODES",
    "MenuChangeError",
    "MenuChangePolicy",
    "MenuSnapshotItem",
    "filter_excluded_candidates",
    "finalize_menu_state",
    "resolve_menu_change",
    "validate_menu_change",
]
