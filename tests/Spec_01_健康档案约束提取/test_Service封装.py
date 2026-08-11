from __future__ import annotations

import importlib

import pytest

from spec01_support import (
    RecordingSessionFactory,
    production_contract,
    profile_factory,
)


def test_非法ID不创建Session(production_contract):
    session_factory = RecordingSessionFactory()
    service = production_contract.ProfileConstraintService(
        session_factory,
        lambda session, profile_id: None,
    )

    with pytest.raises(
        production_contract.ProfileConstraintValidationError
    ) as captured:
        service.extract(0)

    assert captured.value.status_code == 400
    assert session_factory.call_count == 0


def test_档案不存在返回404(production_contract):
    session_factory = RecordingSessionFactory()
    service = production_contract.ProfileConstraintService(
        session_factory,
        lambda session, profile_id: None,
    )

    with pytest.raises(
        production_contract.ProfileConstraintExtractionError
    ) as captured:
        service.extract(25)

    assert captured.value.status_code == 404
    assert session_factory.call_count == 1
    assert session_factory.contexts[0].exit_count == 1


def test_数据库查询失败返回500(production_contract):
    session_factory = RecordingSessionFactory()

    def fail_to_load(session: object, profile_id: int):
        del session, profile_id
        raise production_contract.ProfileRepositoryError("查询失败")

    service = production_contract.ProfileConstraintService(
        session_factory,
        fail_to_load,
    )

    with pytest.raises(
        production_contract.ProfileConstraintExtractionError
    ) as captured:
        service.extract(25)

    assert captured.value.status_code == 500
    assert session_factory.contexts[0].exit_count == 1


def test_Session工厂创建失败返回500(production_contract):
    def fail_to_create_session():
        raise RuntimeError("数据库不可达")

    service = production_contract.ProfileConstraintService(
        fail_to_create_session,
        lambda session, profile_id: None,
    )

    with pytest.raises(
        production_contract.ProfileConstraintExtractionError
    ) as captured:
        service.extract(25)

    assert captured.value.status_code == 500


def test_正常提取后自动退出Session(
    production_contract,
    profile_factory,
):
    profile = profile_factory()
    session_factory = RecordingSessionFactory()
    service = production_contract.ProfileConstraintService(
        session_factory,
        lambda session, profile_id: profile,
    )

    result = service.extract(profile.id)

    assert result["profile_id"] == profile.id
    assert session_factory.call_count == 1
    assert session_factory.contexts[0].enter_count == 1
    assert session_factory.contexts[0].exit_count == 1


def test_旧ORM函数不再对外公开():
    module = importlib.import_module("backend.services.profile_constraints")

    assert not hasattr(module, "extract_profile_constraints")
