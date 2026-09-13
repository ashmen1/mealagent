from __future__ import annotations

from collections.abc import Sequence


def join_chinese_items(values: Sequence[str], conjunction: str) -> str:
    """使用顿号和末项连接词拼接非空中文枚举。"""

    if not values:
        raise ValueError("待拼接值不能为空")
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return conjunction.join(values)
    return "、".join(values[:-1]) + conjunction + values[-1]


__all__ = ["join_chinese_items"]
