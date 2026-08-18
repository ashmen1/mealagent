from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
repo_root_text = str(REPO_ROOT)
if repo_root_text not in sys.path:
    sys.path.insert(0, repo_root_text)


class FakeEngine:
    def __init__(self) -> None:
        self.dispose_count = 0

    def dispose(self) -> None:
        self.dispose_count += 1


class FakeNeo4jDriver:
    def __init__(self) -> None:
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


def patch_create_neo4j_driver(application, driver: FakeNeo4jDriver):
    def create_neo4j_driver(uri: str, user: str, password: str):
        del uri, user, password
        return driver

    application.create_neo4j_driver = create_neo4j_driver
    return driver


def test_应用容器创建一组共享基础设施的Service(monkeypatch):
    application = importlib.import_module("backend.application")
    engine = FakeEngine()
    driver = FakeNeo4jDriver()
    session_factory = lambda: None
    llm_client = lambda prompt: {}
    multi_turn_llm_client = lambda prompt: {}
    observed_urls: list[str] = []

    def create_engine(database_url: str):
        observed_urls.append(database_url)
        return engine

    monkeypatch.setattr(application, "create_database_engine", create_engine)
    monkeypatch.setattr(
        application,
        "create_session_factory",
        lambda received_engine: session_factory,
    )
    monkeypatch.setattr(
        application,
        "create_langchain_constraint_extractor_from_environment",
        lambda: llm_client,
    )
    monkeypatch.setattr(
        application,
        "create_langchain_multi_turn_extractor_from_environment",
        lambda: multi_turn_llm_client,
    )
    patch_create_neo4j_driver(application, driver)

    services = application.create_constraint_services()

    assert observed_urls == [
        "postgresql+psycopg://mealagent:mealagent@127.0.0.1:5432/mealagent"
    ]
    assert services.profile._session_factory is session_factory
    assert services.dialogue._session_factory is session_factory
    assert services.dialogue._llm_client is llm_client
    assert services.dish_filtering._driver is driver
    assert services.multi_turn._session_factory is session_factory
    assert services.multi_turn._llm_client is multi_turn_llm_client
    assert services.confirmation._multi_turn_service is services.multi_turn
    assert (
        services.confirmation._meal_period_service
        is services.multi_turn._meal_period_service
    )

    services.close()
    services.close()
    assert engine.dispose_count == 1
    assert driver.close_count == 1


def test_上下文退出时释放Engine(monkeypatch):
    application = importlib.import_module("backend.application")
    engine = FakeEngine()
    driver = FakeNeo4jDriver()
    monkeypatch.setattr(
        application,
        "create_database_engine",
        lambda database_url: engine,
    )
    monkeypatch.setattr(
        application,
        "create_session_factory",
        lambda received_engine: lambda: None,
    )
    monkeypatch.setattr(
        application,
        "create_langchain_constraint_extractor_from_environment",
        lambda: (lambda prompt: {}),
    )
    monkeypatch.setattr(
        application,
        "create_langchain_multi_turn_extractor_from_environment",
        lambda: (lambda prompt: {}),
    )
    patch_create_neo4j_driver(application, driver)

    with application.create_constraint_services() as services:
        assert services.profile is not None
        assert services.dialogue is not None
        assert services.dish_filtering is not None
        assert engine.dispose_count == 0
        assert driver.close_count == 0

    assert engine.dispose_count == 1
    assert driver.close_count == 1


def test_LLM创建失败时释放已创建的Engine(monkeypatch):
    application = importlib.import_module("backend.application")
    engine = FakeEngine()
    driver = FakeNeo4jDriver()
    expected_error = RuntimeError("LLM创建失败")
    monkeypatch.setattr(
        application,
        "create_database_engine",
        lambda database_url: engine,
    )
    monkeypatch.setattr(
        application,
        "create_session_factory",
        lambda received_engine: lambda: None,
    )
    monkeypatch.setattr(
        application,
        "create_langchain_constraint_extractor_from_environment",
        lambda: (_ for _ in ()).throw(expected_error),
    )
    patch_create_neo4j_driver(application, driver)

    with pytest.raises(RuntimeError) as captured:
        application.create_constraint_services()

    assert captured.value is expected_error
    assert engine.dispose_count == 1
    assert driver.close_count == 1


def test_每次创建都返回独立容器而不使用全局缓存(monkeypatch):
    application = importlib.import_module("backend.application")
    engines: list[FakeEngine] = []

    def create_engine(database_url: str):
        del database_url
        engine = FakeEngine()
        engines.append(engine)
        return engine

    monkeypatch.setattr(application, "create_database_engine", create_engine)
    monkeypatch.setattr(
        application,
        "create_session_factory",
        lambda received_engine: lambda: None,
    )
    monkeypatch.setattr(
        application,
        "create_langchain_constraint_extractor_from_environment",
        lambda: (lambda prompt: {}),
    )
    monkeypatch.setattr(
        application,
        "create_langchain_multi_turn_extractor_from_environment",
        lambda: (lambda prompt: {}),
    )
    patch_create_neo4j_driver(application, FakeNeo4jDriver())

    first = application.create_constraint_services()
    second = application.create_constraint_services()
    try:
        assert first is not second
        assert engines[0] is not engines[1]
    finally:
        first.close()
        second.close()
