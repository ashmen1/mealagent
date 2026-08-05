from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from backend.core.profile_constraint_contract import (
    ProfileConstraintExtractionError,
    ProfileConstraintSource,
    ProfileConstraintValidationError,
    ProfileConstraints,
    normalize_profile_constraints,
    validate_profile_id,
)
from backend.infrastructure.database.profile_repository import (
    ProfileRepositoryError,
    load_user_profile,
)


ProfileLoader = Callable[[Session, int], ProfileConstraintSource | None]
SessionFactory = Callable[[], Session]


class ProfileConstraintService:
    """按用户档案ID提取健康档案约束。"""

    def __init__(
        self,
        session_factory: SessionFactory,
        profile_loader: ProfileLoader = load_user_profile,
    ) -> None:
        if not callable(session_factory):
            raise ProfileConstraintExtractionError(500, "Session工厂无效")
        if not callable(profile_loader):
            raise ProfileConstraintExtractionError(500, "健康档案Repository无效")
        self._session_factory = session_factory
        self._profile_loader = profile_loader

    def extract(self, profile_id: int) -> ProfileConstraints:
        """加载指定用户健康档案并提取约束。"""

        validated_profile_id = validate_profile_id(profile_id)
        try:
            with self._session_factory() as session:
                profile = self._profile_loader(session, validated_profile_id)
                if profile is None:
                    raise ProfileConstraintExtractionError(
                        404,
                        f"用户健康档案不存在：{validated_profile_id}",
                    )
                return normalize_profile_constraints(profile)
        except ProfileRepositoryError as exc:
            raise ProfileConstraintExtractionError(500, str(exc)) from exc


__all__ = [
    "ProfileConstraintExtractionError",
    "ProfileConstraintService",
    "ProfileConstraints",
    "ProfileConstraintValidationError",
]
