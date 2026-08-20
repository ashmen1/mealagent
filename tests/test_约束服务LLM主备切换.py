"""约束服务LLM主备切换测试。

主模型配额耗尽(429)时自动切换到备用模型；未配置备用时行为不变。
"""

import pytest

from backend.infrastructure.llm.langchain_constraints import (
    _FallbackChatModel,
    _create_backup_chat_model_from_environment,
    _is_quota_exhausted,
    create_chat_model_from_environment,
)


class _QuotaExhaustedError(Exception):
    status_code = 429


class _ServerError(Exception):
    status_code = 500


class _StubModel:
    """可配置抛出异常或返回固定结果的最小ChatModel替身。"""

    def __init__(self, name, raise_error=None, result=None):
        self.name = name
        self.raise_error = raise_error
        self.result = result
        self.invoke_count = 0
        self.structured_count = 0

    def with_structured_output(self, schema, **kwargs):
        self.structured_count += 1
        return _StubStructured(self)

    def invoke(self, prompt):
        self.invoke_count += 1
        if self.raise_error is not None:
            raise self.raise_error
        return self.result


class _StubStructured:
    def __init__(self, model):
        self.model = model

    def invoke(self, prompt):
        return self.model.invoke(prompt)


def test_主模型正常时不触发备用():
    primary = _StubModel("primary", result="主结果")
    backup = _StubModel("backup", result="备结果")
    model = _FallbackChatModel(primary, backup)
    assert model.invoke("prompt") == "主结果"
    assert primary.invoke_count == 1
    assert backup.invoke_count == 0


def test_主模型配额耗尽时自动切换备用():
    primary = _StubModel("primary", raise_error=_QuotaExhaustedError())
    backup = _StubModel("backup", result="备结果")
    model = _FallbackChatModel(primary, backup)
    assert model.invoke("prompt") == "备结果"
    assert primary.invoke_count == 1
    assert backup.invoke_count == 1


def test_主模型非配额错误时原样抛出():
    primary = _StubModel("primary", raise_error=_ServerError())
    backup = _StubModel("backup", result="备结果")
    model = _FallbackChatModel(primary, backup)
    with pytest.raises(_ServerError):
        model.invoke("prompt")
    assert backup.invoke_count == 0


def test_备用模型也失败时抛出备用异常():
    primary = _StubModel("primary", raise_error=_QuotaExhaustedError())
    backup = _StubModel("backup", raise_error=_ServerError())
    model = _FallbackChatModel(primary, backup)
    with pytest.raises(_ServerError):
        model.invoke("prompt")


def test_结构化输出后切换仍生效():
    primary = _StubModel("primary", raise_error=_QuotaExhaustedError())
    backup = _StubModel("backup", result="备结果")
    model = _FallbackChatModel(primary, backup)
    structured = model.with_structured_output({"type": "object"})
    assert structured.invoke("prompt") == "备结果"
    assert primary.structured_count == 1
    assert backup.structured_count == 1


def test_无备用时结构化输出直接透传():
    primary = _StubModel("primary", result="主结果")
    model = _FallbackChatModel(primary)
    structured = model.with_structured_output({"type": "object"})
    assert structured.invoke("prompt") == "主结果"


def test_配额耗尽判定():
    assert _is_quota_exhausted(_QuotaExhaustedError())
    assert not _is_quota_exhausted(_ServerError())
    assert _is_quota_exhausted(
        Exception("Error code: 429 - quota exhausted")
    )
    assert not _is_quota_exhausted(Exception("普通错误"))


def test_未配置备用时返回None(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL_BACKUP", raising=False)
    monkeypatch.delenv("LLM_AUTH_TOKEN_BACKUP", raising=False)
    monkeypatch.delenv("LLM_MODEL_BACKUP", raising=False)
    assert _create_backup_chat_model_from_environment() is None


def test_配置完整备用时创建备用模型(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_BACKUP", "openai")
    monkeypatch.setenv("LLM_BASE_URL_BACKUP", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_AUTH_TOKEN_BACKUP", "sk-test")
    monkeypatch.setenv("LLM_MODEL_BACKUP", "deepseek-v4-flash")
    backup = _create_backup_chat_model_from_environment()
    assert backup is not None
    assert backup.model_name == "deepseek-v4-flash"
    assert "deepseek" in backup.openai_api_base


def test_未配置备用时创建普通模型(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL_BACKUP", raising=False)
    monkeypatch.delenv("LLM_AUTH_TOKEN_BACKUP", raising=False)
    monkeypatch.delenv("LLM_MODEL_BACKUP", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("LLM_AUTH_TOKEN", "sk-main")
    monkeypatch.setenv("LLM_MODEL", "main-model")
    model = create_chat_model_from_environment()
    assert not isinstance(model, _FallbackChatModel)


def test_配置备用时创建主备包装模型(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("LLM_AUTH_TOKEN", "sk-main")
    monkeypatch.setenv("LLM_MODEL", "main-model")
    monkeypatch.setenv("LLM_PROVIDER_BACKUP", "openai")
    monkeypatch.setenv("LLM_BASE_URL_BACKUP", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_AUTH_TOKEN_BACKUP", "sk-backup")
    monkeypatch.setenv("LLM_MODEL_BACKUP", "deepseek-v4-flash")
    model = create_chat_model_from_environment()
    assert isinstance(model, _FallbackChatModel)
