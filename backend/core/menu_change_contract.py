from __future__ import annotations

from typing import Final, Literal, TypedDict


MENU_CHANGE_MODES: Final = (
    "none",
    "replace_all",
    "replace_partial",
    "replace_specific",
    "restore_specific",
)
MENU_CHANGE_FIELDS: Final = (
    "mode",
    "replace_count",
    "target_positions",
    "target_recipe_names",
    "replacement_recipe_name",
    "unresolved_target",
    "evidence",
)
MENU_CHANGE_POLICY_FIELDS: Final = (
    "previous_recipe_names",
    "required_previous_count",
    "required_recipe_names",
    "forbidden_recipe_names",
)

MenuChangeMode = Literal[*MENU_CHANGE_MODES]


class MenuChangeIntent(TypedDict):
    """单轮对最近成功菜单的操作意图。"""

    mode: MenuChangeMode
    replace_count: int | None
    target_positions: list[int]
    target_recipe_names: list[str]
    replacement_recipe_name: str | None
    unresolved_target: str | None
    evidence: str | None


class MenuSnapshotItem(TypedDict):
    """会话保存的最小菜单条目。"""

    position: int
    dish_constraint_index: int
    recipe_name: str


class MenuChangePolicy(TypedDict):
    """传给菜单求解器的换菜硬约束。"""

    previous_recipe_names: list[str]
    required_previous_count: int | None
    required_recipe_names: list[str]
    forbidden_recipe_names: list[str]


class MenuChangeResult(TypedDict):
    """成功规划后的菜单变化摘要。"""

    mode: Literal[
        "replace_all",
        "replace_partial",
        "replace_specific",
        "restore_specific",
    ]
    retained_recipe_names: list[str]
    removed_recipe_names: list[str]
    added_recipe_names: list[str]


def build_none_menu_change() -> MenuChangeIntent:
    """返回互不共享的普通对话菜单意图。"""

    return {
        "mode": "none",
        "replace_count": None,
        "target_positions": [],
        "target_recipe_names": [],
        "replacement_recipe_name": None,
        "unresolved_target": None,
        "evidence": None,
    }


__all__ = [
    "MENU_CHANGE_FIELDS",
    "MENU_CHANGE_MODES",
    "MENU_CHANGE_POLICY_FIELDS",
    "MenuChangeIntent",
    "MenuChangeMode",
    "MenuChangePolicy",
    "MenuChangeResult",
    "MenuSnapshotItem",
    "build_none_menu_change",
]
