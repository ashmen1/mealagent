from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker


SQLITE_DATABASE_URL = "sqlite+pysqlite:///:memory:"


@pytest.fixture
def database_contract(add_repo_to_python_path):
    del add_repo_to_python_path
    try:
        module = importlib.import_module(
            "backend.infrastructure.database.database"
        )
        create_database_engine = module.create_database_engine
        create_session_factory = module.create_session_factory
        configuration_error = module.DatabaseConfigurationError
    except (ModuleNotFoundError, AttributeError) as exc:
        pytest.fail(
            "缺少 Spec_00 约定的数据库工厂接口："
            "backend.infrastructure.database.database."
            "create_database_engine、"
            "create_session_factory 或 DatabaseConfigurationError；"
            f"原始错误：{exc}",
            pytrace=False,
        )

    return SimpleNamespace(
        module=module,
        create_database_engine=create_database_engine,
        create_session_factory=create_session_factory,
        DatabaseConfigurationError=configuration_error,
    )


def test_使用显式URL创建同步Engine(database_contract):
    engine = database_contract.create_database_engine(SQLITE_DATABASE_URL)
    try:
        assert isinstance(engine, Engine)
        assert str(engine.url) == SQLITE_DATABASE_URL
        assert engine.pool._pre_ping is True
    finally:
        engine.dispose()


def test_显式URL不被环境变量覆盖(database_contract, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "invalid-environment-value")

    engine = database_contract.create_database_engine(SQLITE_DATABASE_URL)
    try:
        assert str(engine.url) == SQLITE_DATABASE_URL
    finally:
        engine.dispose()


@pytest.mark.parametrize("bad_url", [None, 1, "", "  "])
def test_数据库URL类型或空值错误(database_contract, bad_url):
    with pytest.raises(database_contract.DatabaseConfigurationError):
        database_contract.create_database_engine(bad_url)


def test_数据库URL格式错误(database_contract):
    with pytest.raises(database_contract.DatabaseConfigurationError):
        database_contract.create_database_engine("not-a-database-url")


def test_配置错误属于ValueError(database_contract):
    assert issubclass(database_contract.DatabaseConfigurationError, ValueError)


def test_创建绑定到指定Engine的Session工厂(database_contract):
    engine = database_contract.create_database_engine(SQLITE_DATABASE_URL)
    try:
        factory = database_contract.create_session_factory(engine)
        assert isinstance(factory, sessionmaker)

        first_session = factory()
        second_session = factory()
        try:
            assert first_session is not second_session
            assert first_session.get_bind() is engine
            assert second_session.get_bind() is engine
        finally:
            first_session.close()
            second_session.close()
    finally:
        engine.dispose()


@pytest.mark.parametrize("bad_engine", [None, "engine", object()])
def test_Session工厂拒绝非同步Engine(database_contract, bad_engine):
    with pytest.raises(TypeError, match="Engine"):
        database_contract.create_session_factory(bad_engine)


def test_数据库工厂不自动创建表(database_contract):
    engine = database_contract.create_database_engine(SQLITE_DATABASE_URL)
    try:
        database_contract.create_session_factory(engine)
        assert inspect(engine).get_table_names() == []
    finally:
        engine.dispose()


def test_事务由调用方显式回滚(database_contract):
    models_module = importlib.import_module(
        "backend.infrastructure.database.models"
    )
    Base = models_module.Base
    Ingredient = models_module.Ingredient

    engine = database_contract.create_database_engine(SQLITE_DATABASE_URL)
    Base.metadata.create_all(engine)
    try:
        factory = database_contract.create_session_factory(engine)
        with factory() as session:
            session.add(Ingredient(id=1, name="事务测试食材", aliases=[]))
            session.flush()
            session.rollback()

        with factory() as session:
            ingredient_count = session.scalar(
                select(func.count()).select_from(Ingredient)
            )
            assert ingredient_count == 0
    finally:
        engine.dispose()
