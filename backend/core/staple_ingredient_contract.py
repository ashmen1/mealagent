from __future__ import annotations

from collections.abc import Iterable
from typing import Final


RICE_STAPLE_FAMILY: Final[tuple[str, ...]] = (
    "米饭",
    "大米",
    "大米饭",
    "糙米",
    "黑米",
    "黑糯米",
    "红米",
    "粳米",
    "香米",
    "糯米",
    "血糯米",
    "长香糯米",
    "紫米",
    "梗米粉",
    "黑米粉",
    "糯米粉",
    "粘米粉",
    "米粉",
)


def expand_staple_term(value: str) -> tuple[str, ...]:
    """按受控规则展开主食族词。"""

    return RICE_STAPLE_FAMILY if value == "米饭" else (value,)


def expand_staple_items(values: Iterable[str]) -> tuple[str, ...]:
    """按输入顺序展开多个主食词并去重。"""

    return tuple(
        dict.fromkeys(
            expanded
            for value in values
            for expanded in expand_staple_term(value)
        )
    )


def has_staple_overlap(
    required_items: Iterable[str],
    excluded_items: Iterable[str],
) -> bool:
    """判断主食正向与排除条件展开后是否重叠。"""

    required = set(expand_staple_items(required_items))
    excluded = set(expand_staple_items(excluded_items))
    return bool(required & excluded)


__all__ = [
    "RICE_STAPLE_FAMILY",
    "expand_staple_items",
    "expand_staple_term",
    "has_staple_overlap",
]
