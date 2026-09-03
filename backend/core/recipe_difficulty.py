from __future__ import annotations

from typing import Final, Literal


RecipeDifficulty = Literal["简单", "中等", "复杂"]

SIMPLE_MAX_TOTAL_TIME_MINUTES: Final = 20
SIMPLE_MAX_ATOMIC_STEPS: Final = 8
SIMPLE_MAX_INGREDIENT_COUNT: Final = 8

COMPLEX_TOTAL_TIME_MINUTES_THRESHOLD: Final = 60
COMPLEX_ATOMIC_STEPS_THRESHOLD: Final = 15
COMPLEX_INGREDIENT_COUNT_THRESHOLD: Final = 18


def derive_recipe_difficulty(
    *,
    total_time_minutes: int,
    atomic_step_count: int,
    ingredient_count: int,
) -> RecipeDifficulty:
    """按统一阈值确定性派生菜谱难度。"""

    if (
        total_time_minutes <= SIMPLE_MAX_TOTAL_TIME_MINUTES
        and atomic_step_count <= SIMPLE_MAX_ATOMIC_STEPS
        and ingredient_count <= SIMPLE_MAX_INGREDIENT_COUNT
    ):
        return "简单"
    if (
        total_time_minutes > COMPLEX_TOTAL_TIME_MINUTES_THRESHOLD
        or atomic_step_count > COMPLEX_ATOMIC_STEPS_THRESHOLD
        or ingredient_count > COMPLEX_INGREDIENT_COUNT_THRESHOLD
    ):
        return "复杂"
    return "中等"


__all__ = [
    "COMPLEX_ATOMIC_STEPS_THRESHOLD",
    "COMPLEX_INGREDIENT_COUNT_THRESHOLD",
    "COMPLEX_TOTAL_TIME_MINUTES_THRESHOLD",
    "RecipeDifficulty",
    "SIMPLE_MAX_ATOMIC_STEPS",
    "SIMPLE_MAX_INGREDIENT_COUNT",
    "SIMPLE_MAX_TOTAL_TIME_MINUTES",
    "derive_recipe_difficulty",
]
