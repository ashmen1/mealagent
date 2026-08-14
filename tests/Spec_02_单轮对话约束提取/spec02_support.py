from __future__ import annotations

import copy
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


SPEC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPEC_DIR.parents[1]
repo_root_text = str(REPO_ROOT)
if repo_root_text not in sys.path:
    sys.path.insert(0, repo_root_text)

from backend.infrastructure.database.models import Base, Ingredient


TOP_LEVEL_FIELDS = (
    "dialogue_id",
    "meal_periods",
    "diner_count",
    "max_total_time_minutes",
    "available_ingredients",
    "dishes",
    "evidence",
)

DISH_FIELDS = (
    "count",
    "dish_type",
    "taste_preferences",
    "cuisines",
    "effects",
    "special_populations",
    "required_ingredients",
)


_UNSET = object()


class FakeLLMClient:
    """记录调用信息并返回预设结构化结果的假LLM约束提取器。"""

    def __init__(
        self,
        response: object = _UNSET,
        error: BaseException | None = None,
        responses: list[object] | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.responses = list(responses) if responses is not None else None
        self.prompts: list[str] = []

    @property
    def call_count(self) -> int:
        return len(self.prompts)

    def __call__(self, prompt: str) -> Any:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        if self.responses is not None:
            if not self.responses:
                raise AssertionError("FakeLLMClient响应序列已耗尽")
            return self.responses.pop(0)
        if self.response is _UNSET:
            raise AssertionError("FakeLLMClient未配置响应")
        return self.response


def build_empty_dish() -> dict[str, Any]:
    return {
        "count": None,
        "dish_type": "未指定",
        "taste_preferences": {},
        "cuisines": [],
        "effects": [],
        "special_populations": [],
        "required_ingredients": [],
    }


def build_empty_result(dialogue_id: int = 1) -> dict[str, Any]:
    return {
        "dialogue_id": dialogue_id,
        "meal_periods": [],
        "diner_count": None,
        "max_total_time_minutes": None,
        "available_ingredients": [],
        "dishes": [build_empty_dish()],
        "evidence": {},
    }


@pytest.fixture
def production_contract():
    try:
        module = importlib.import_module(
            "backend.services.dialogue_constraints"
        )
        adapter_module = importlib.import_module(
            "backend.infrastructure.llm.langchain_constraints"
        )
        dialogue_service = module.DialogueConstraintService
        extraction_error = module.DialogueConstraintExtractionError
        langchain_extractor = adapter_module.LangChainConstraintExtractor
        create_real_extractor = (
            adapter_module.create_langchain_constraint_extractor_from_environment
        )
    except (ModuleNotFoundError, AttributeError) as exc:
        pytest.fail(
            "缺少 Spec_02 约定的生产接口："
            "backend.services.dialogue_constraints."
            "DialogueConstraintService 或 "
            "DialogueConstraintExtractionError，或 "
            "backend.infrastructure.llm.langchain_constraints."
            "LangChainConstraintExtractor / "
            "create_langchain_constraint_extractor_from_environment；"
            f"原始错误：{exc}",
            pytrace=False,
        )

    return SimpleNamespace(
        DialogueConstraintService=dialogue_service,
        DialogueConstraintExtractionError=extraction_error,
        LangChainConstraintExtractor=langchain_extractor,
        create_langchain_constraint_extractor_from_environment=(
            create_real_extractor
        ),
    )


@pytest.fixture
def dialogue_factory() -> Callable[..., dict[str, Any]]:
    def create_dialogue(**overrides: Any) -> dict[str, Any]:
        values: dict[str, Any] = {
            "id": 1,
            "turn_count": 1,
            "user_messages": ["今晚吃啥比较好？"],
        }
        values.update(copy.deepcopy(overrides))
        return values

    return create_dialogue


@pytest.fixture
def ingredient_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add_all(
        [
            Ingredient(id=1, name="番茄", category="蔬菜", aliases=[]),
            Ingredient(id=2, name="鸡蛋", category="蛋奶", aliases=[]),
            Ingredient(id=3, name="土豆", category="蔬菜", aliases=[]),
            Ingredient(id=4, name="米饭", category="粮食", aliases=[]),
        ]
    )
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def invoke_extract(production_contract, ingredient_session):
    def invoke(
        dialogue: dict[str, Any],
        llm_client: FakeLLMClient,
        session: Session | object | None = None,
    ) -> dict[str, Any]:
        active_session = ingredient_session if session is None else session
        service = production_contract.DialogueConstraintService(
            lambda: active_session,
            llm_client,
        )
        return service.extract(dialogue)

    return invoke


@pytest.fixture
def assert_extraction_error(production_contract, invoke_extract):
    def assert_error(
        dialogue: dict[str, Any],
        llm_client: FakeLLMClient,
        expected_status_code: int,
        session: Session | object | None = None,
    ):
        with pytest.raises(
            production_contract.DialogueConstraintExtractionError
        ) as captured:
            invoke_extract(dialogue, llm_client, session)
        assert captured.value.status_code == expected_status_code
        return captured.value

    return assert_error
