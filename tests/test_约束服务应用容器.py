from __future__ import annotations

import importlib

import pytest


class FakeEngine:
    def __init__(self) -> None:
        self.dispose_count = 0

    def dispose(self) -> None:
        self.dispose_count += 1


def test_应用容器创建一组共享基础设施的Service(monkeypatch):
    application = importlib.import_module("backend.application")
    engine = FakeEngine()
    session_factory = lambda: None
    llm_client = lambda prompt: {}
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

    services = application.create_constraint_services()

    assert observed_urls == [
        "postgresql+psycopg://mealagent:mealagent@127.0.0.1:5432/mealagent"
    ]
    assert services.profile._session_factory is session_factory
    assert services.dialogue._session_factory is session_factory
    assert services.dialogue._llm_client is llm_client

    services.close()
    services.close()
    assert engine.dispose_count == 1


def test_上下文退出时释放Engine(monkeypatch):
    application = importlib.import_module("backend.application")
    engine = FakeEngine()
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

    with application.create_constraint_services() as services:
        assert services.profile is not None
        assert services.dialogue is not None
        assert engine.dispose_count == 0

    assert engine.dispose_count == 1


def test_LLM创建失败时释放已创建的Engine(monkeypatch):
    application = importlib.import_module("backend.application")
    engine = FakeEngine()
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

    with pytest.raises(RuntimeError) as captured:
        application.create_constraint_services()

    assert captured.value is expected_error
    assert engine.dispose_count == 1


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

    first = application.create_constraint_services()
    second = application.create_constraint_services()
    try:
        assert first is not second
        assert engines[0] is not engines[1]
    finally:
        first.close()
        second.close()
