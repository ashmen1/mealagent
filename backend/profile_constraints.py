from __future__ import annotations

from typing import Final, TypedDict

from .storage.models import UserProfile


class ProfileConstraints(TypedDict):
    """供后续业务使用的统一健康档案约束。"""

    profile_id: int
    special_populations: list[str]
    taste_preferences: dict[str, bool]
    allergens: list[str]


TasteToken = tuple[str, str, bool]


VALID_PROFILE_ID_MIN: Final[int] = 1
VALID_PROFILE_ID_MAX: Final[int] = 50

VALID_SPECIAL_POPULATIONS: Final[frozenset[str]] = frozenset(
    {
        "备孕",
        "哺乳期",
        "高尿酸",
        "高血糖",
        "高血压",
        "孕妇",
    }
)

VALID_ALLERGENS: Final[frozenset[str]] = frozenset(
    {
        "豆类",
        "海鲜",
        "花生",
        "鸡蛋",
        "坚果",
        "芒果",
        "牛奶",
        "啤酒",
        "虾",
        "蟹类",
    }
)

TASTE_TOKENS: Final[tuple[TasteToken, ...]] = (
    ("不甜", "is_sweet", False),
    ("不咸", "is_salty", False),
    ("清淡", "is_light", True),
    ("甜", "is_sweet", True),
    ("辣", "is_spicy", True),
    ("咸", "is_salty", True),
    ("酸", "is_sour", True),
)

TASTE_SEPARATORS: Final[frozenset[str]] = frozenset({"、", "，", ","})
NO_CONSTRAINT_VALUE: Final[str] = "无"
IGNORED_TASTE_VALUES: Final[frozenset[str]] = frozenset({"", "无", "忽略"})


class ProfileConstraintValidationError(Exception):
    """健康档案字段或取值不符合约束提取规格。"""

    status_code = 400


def extract_profile_constraints(profile: UserProfile) -> ProfileConstraints:
    """从一条用户健康档案记录提取统一约束结构。"""

    if not isinstance(profile, UserProfile):
        raise ProfileConstraintValidationError("输入必须是一条 UserProfile 记录")

    profile_id = _validate_profile_id(profile.id)
    special_populations = _normalize_array_constraint(
        profile.special_populations,
        field_name="special_populations",
        valid_values=VALID_SPECIAL_POPULATIONS,
    )
    taste_preferences = _normalize_taste_preferences(profile.taste_preference)
    allergens = _normalize_array_constraint(
        profile.allergens,
        field_name="allergens",
        valid_values=VALID_ALLERGENS,
    )

    return {
        "profile_id": profile_id,
        "special_populations": special_populations,
        "taste_preferences": taste_preferences,
        "allergens": allergens,
    }


def _validate_profile_id(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProfileConstraintValidationError("profile_id 必须是整数")
    if not VALID_PROFILE_ID_MIN <= value <= VALID_PROFILE_ID_MAX:
        raise ProfileConstraintValidationError(
            f"profile_id 必须在 {VALID_PROFILE_ID_MIN} 到 "
            f"{VALID_PROFILE_ID_MAX} 之间"
        )
    return value


def _normalize_array_constraint(
    value: object,
    *,
    field_name: str,
    valid_values: frozenset[str],
) -> list[str]:
    if not isinstance(value, list):
        raise ProfileConstraintValidationError(f"{field_name} 必须是数组")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ProfileConstraintValidationError(
                f"{field_name} 中的每个值都必须是字符串"
            )
        if item not in seen:
            normalized.append(item)
            seen.add(item)

    if NO_CONSTRAINT_VALUE in seen:
        if len(seen) > 1:
            raise ProfileConstraintValidationError(
                f"{field_name} 中的“无”不能与其他值同时出现"
            )
        return []

    unknown_values = [item for item in normalized if item not in valid_values]
    if unknown_values:
        raise ProfileConstraintValidationError(
            f"{field_name} 出现未配置值：{', '.join(unknown_values)}"
        )

    return normalized


def _normalize_taste_preferences(value: object) -> dict[str, bool]:
    if not isinstance(value, str):
        raise ProfileConstraintValidationError("taste_preference 必须是字符串")
    if value in IGNORED_TASTE_VALUES:
        return {}

    compact_value = "".join(
        character for character in value if character not in TASTE_SEPARATORS
    )
    if NO_CONSTRAINT_VALUE in compact_value:
        raise ProfileConstraintValidationError(
            "taste_preference 中的“无”不能与其他值同时出现"
        )

    preferences: dict[str, bool] = {}
    position = 0
    while position < len(compact_value):
        text, field_name, enabled = _match_taste_token(
            compact_value,
            position,
            original_value=value,
        )
        if field_name in preferences and preferences[field_name] != enabled:
            taste_name = text.removeprefix("不")
            raise ProfileConstraintValidationError(
                f"taste_preference 中的{taste_name}同时出现肯定和否定"
            )
        preferences[field_name] = enabled
        position += len(text)

    return preferences


def _match_taste_token(
    value: str,
    position: int,
    *,
    original_value: str,
) -> TasteToken:
    for token in TASTE_TOKENS:
        if value.startswith(token[0], position):
            return token
    raise ProfileConstraintValidationError(
        f"taste_preference 出现未配置值：{original_value}"
    )


__all__ = [
    "ProfileConstraints",
    "ProfileConstraintValidationError",
    "extract_profile_constraints",
]
