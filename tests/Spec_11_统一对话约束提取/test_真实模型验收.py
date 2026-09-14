from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from backend.infrastructure.health import check_chat_model
from backend.infrastructure.llm.langchain_constraints import (
    ConfiguredChatModel,
    LangChainConstraintExtractor,
    _create_configured_chat_models,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DIALOGUES_PATH = REPO_ROOT / "datas" / "raw" / "对话用例.json"
BUSINESS_FIELDS = (
    "meal_periods",
    "diner_count",
    "total_dish_count",
    "max_total_time_minutes",
    "max_difficulty",
    "available_ingredients",
    "dishes",
)
FOCUS_DIALOGUE_IDS = (8, 10, 12, 14, 20)


def _load_environment() -> None:
    """加载真实模型配置，不覆盖调用进程显式提供的环境变量。"""

    env_path = REPO_ROOT / ".env"
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


@pytest.fixture(scope="module")
def real_models() -> tuple[ConfiguredChatModel, ConfiguredChatModel]:
    if os.environ.get("RUN_DIALOGUE_LLM_ACCEPTANCE") != "1":
        pytest.skip("设置RUN_DIALOGUE_LLM_ACCEPTANCE=1后运行真实模型验收")
    _load_environment()
    primary, backup = _create_configured_chat_models()
    if backup is None:
        pytest.fail("真实模型验收要求配置备用模型")
    return primary, backup


class _RecordingExtractor:
    """记录每次真实模型调用的结果、错误和耗时，用于首调失败诊断。"""

    def __init__(self, model: ConfiguredChatModel) -> None:
        self.model_name = model.model_name
        self._extractor = LangChainConstraintExtractor(model.chat_model)
        self.records: list[dict[str, Any]] = []

    @property
    def call_count(self) -> int:
        return len(self.records)

    def __call__(self, prompt: list[dict[str, str]]) -> object:
        started_at = time.perf_counter()
        try:
            result = self._extractor(prompt)
        except Exception as exc:
            self.records.append(
                {
                    "elapsed_seconds": round(time.perf_counter() - started_at, 3),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            raise
        self.records.append(
            {
                "elapsed_seconds": round(time.perf_counter() - started_at, 3),
                "result": result,
            }
        )
        return result


def _dish(**overrides: Any) -> dict[str, Any]:
    dish = {
        "count": None,
        "dish_type": "未指定",
        "taste_preferences": {},
        "cuisines": [],
        "effects": [],
        "special_populations": [],
        "required_ingredient_groups": [],
        "required_staple_ingredients": None,
        "excluded_staple_ingredients": [],
    }
    dish.update(overrides)
    return dish


def _state(**overrides: Any) -> dict[str, Any]:
    state = {
        "meal_periods": [],
        "diner_count": None,
        "total_dish_count": None,
        "max_total_time_minutes": None,
        "max_difficulty": None,
        "available_ingredients": [],
        "dishes": [_dish()],
    }
    state.update(overrides)
    return state


def _expected_dialogues() -> dict[int, list[dict[str, Any]]]:
    family_dishes = [
        _dish(count=4, dish_type="菜"),
        _dish(count=1, dish_type="汤"),
    ]
    family_limited_dishes = [
        _dish(
            count=4,
            dish_type="菜",
            taste_preferences={"is_spicy": False},
            special_populations=["儿童", "老人"],
        ),
        _dish(
            count=1,
            dish_type="汤",
            taste_preferences={"is_spicy": False},
            special_populations=["儿童", "老人"],
        ),
    ]
    opposing = [
        _dish(taste_preferences={"is_spicy": True}),
        _dish(taste_preferences={"is_spicy": False}),
    ]
    opposing_with_main = [
        _dish(
            dish_type="菜",
            taste_preferences={"is_spicy": True},
            required_ingredient_groups=[
                {
                    "match": "any",
                    "items": [
                        {"kind": "ingredient", "value": "鱼"},
                        {"kind": "ingredient", "value": "鸡翅"},
                    ],
                }
            ],
        ),
        _dish(taste_preferences={"is_spicy": False}),
    ]
    return {
        1: [_state(meal_periods=["晚餐"])],
        2: [_state(meal_periods=["早餐"], max_difficulty="简单")],
        3: [
            _state(
                meal_periods=["午餐"],
                dishes=[_dish(taste_preferences={"is_light": True})],
            )
        ],
        4: [_state(meal_periods=["晚餐"], diner_count=2)],
        5: [
            _state(
                meal_periods=["午餐"],
                dishes=[_dish(special_populations=["上班族"])],
            )
        ],
        6: [
            _state(
                meal_periods=["晚餐"],
                max_total_time_minutes=30,
                dishes=[_dish(special_populations=["上班族"])],
            )
        ],
        7: [_state(available_ingredients=["番茄", "鸡蛋", "土豆"])],
        8: [
            _state(
                meal_periods=["晚餐"],
                total_dish_count=2,
                dishes=[
                    _dish(
                        count=1,
                        dish_type="主食",
                        required_ingredient_groups=[
                            {
                                "match": "all",
                                "items": [{"kind": "concept", "value": "面"}],
                            }
                        ],
                    ),
                    _dish(
                        count=1,
                        dish_type="小菜",
                        taste_preferences={"is_light": True},
                    ),
                ],
            )
        ],
        9: [_state(max_difficulty="中等")],
        10: [_state(meal_periods=["晚餐"])],
        11: [_state(meal_periods=["晚餐"], diner_count=4)],
        12: [_state(total_dish_count=5, dishes=family_dishes)],
        13: [_state(dishes=[_dish(effects=["养胃健胃消食"])])],
        14: [_state(total_dish_count=5, dishes=family_limited_dishes)],
        15: [
            _state(meal_periods=["晚餐"]),
            _state(
                meal_periods=["晚餐"],
                dishes=[
                    _dish(
                        taste_preferences={
                            "is_spicy": False,
                            "is_light": True,
                        }
                    )
                ],
            ),
        ],
        16: [
            _state(meal_periods=["午餐"]),
            _state(
                meal_periods=["午餐"],
                diner_count=2,
                dishes=[_dish(effects=["减脂"])],
            ),
        ],
        17: [
            _state(meal_periods=["早餐"]),
            _state(
                meal_periods=["早餐"],
                max_total_time_minutes=10,
                dishes=[_dish(taste_preferences={"is_sweet": False})],
            ),
        ],
        18: [
            _state(),
            _state(diner_count=6, max_difficulty="中等"),
        ],
        19: [
            _state(meal_periods=["晚餐"]),
            _state(meal_periods=["晚餐"]),
            _state(meal_periods=["晚餐"], max_difficulty="简单"),
        ],
        20: [
            _state(meal_periods=["晚餐"], diner_count=2),
            _state(meal_periods=["晚餐"], diner_count=2, dishes=opposing),
            _state(
                meal_periods=["晚餐"],
                diner_count=2,
                dishes=opposing_with_main,
            ),
            _state(
                meal_periods=["晚餐"],
                diner_count=2,
                max_total_time_minutes=45,
                max_difficulty="中等",
                dishes=opposing_with_main,
            ),
        ],
    }


def _load_dialogues() -> dict[int, list[str]]:
    raw = json.loads(DIALOGUES_PATH.read_text(encoding="utf-8"))
    return {item["id"]: item["user_messages"] for item in raw}


def _business_state(merged: dict[str, Any]) -> dict[str, Any]:
    return {field: merged[field] for field in BUSINESS_FIELDS}


def _collect_differences(
    expected: object,
    actual: object,
    path: str = "$",
) -> list[str]:
    if type(expected) is not type(actual):
        return [f"{path}: expected={expected!r}, actual={actual!r}"]
    if isinstance(expected, dict):
        differences: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            if key not in expected:
                differences.append(f"{path}.{key}: unexpected={actual[key]!r}")
            elif key not in actual:
                differences.append(f"{path}.{key}: missing")
            else:
                differences.extend(
                    _collect_differences(expected[key], actual[key], f"{path}.{key}")
                )
        return differences
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return [f"{path}: expected={expected!r}, actual={actual!r}"]
        differences = []
        for index, (expected_item, actual_item) in enumerate(zip(expected, actual)):
            differences.extend(
                _collect_differences(
                    expected_item,
                    actual_item,
                    f"{path}[{index}]",
                )
            )
        return differences
    return [] if expected == actual else [
        f"{path}: expected={expected!r}, actual={actual!r}"
    ]


def _run_dialogues(
    *,
    model: ConfiguredChatModel,
    dialogue_ids: tuple[int, ...],
    service_builder,
    session_factory,
    profile_id: int,
    clock,
) -> None:
    dialogues = _load_dialogues()
    expectations = _expected_dialogues()
    extractor = _RecordingExtractor(model)
    service = service_builder(session_factory, extractor, clock)

    for dialogue_id in dialogue_ids:
        session_id = service.create_session(profile_id)
        previous: dict[str, Any] | None = None
        messages = dialogues[dialogue_id]
        expected_turns = expectations[dialogue_id]
        assert len(messages) == len(expected_turns)
        for turn_index, (message, expected) in enumerate(
            zip(messages, expected_turns),
            start=1,
        ):
            calls_before = extractor.call_count
            started_at = time.perf_counter()
            try:
                response = service.submit_turn(session_id, message)
            except Exception as exc:
                elapsed = round(time.perf_counter() - started_at, 3)
                pytest.fail(
                    "真实提取失败\n"
                    f"模型：{model.model_name}\n"
                    f"对话：{dialogue_id}:{turn_index}\n"
                    f"输入：{message}\n"
                    f"上一状态：{json.dumps(previous, ensure_ascii=False)}\n"
                    f"校验错误：{type(exc).__name__}: {exc}\n"
                    f"模型记录：{json.dumps(extractor.records[calls_before:], ensure_ascii=False)}\n"
                    f"耗时：{elapsed}秒"
                )

            elapsed = round(time.perf_counter() - started_at, 3)
            actual = _business_state(response["merged_constraints"])
            differences = _collect_differences(expected, actual)
            calls_used = extractor.call_count - calls_before
            if calls_used != 1 or differences:
                pytest.fail(
                    "真实提取未通过验收\n"
                    f"模型：{model.model_name}\n"
                    f"对话：{dialogue_id}:{turn_index}\n"
                    f"输入：{message}\n"
                    f"上一状态：{json.dumps(previous, ensure_ascii=False)}\n"
                    f"字段差异：{json.dumps(differences, ensure_ascii=False)}\n"
                    f"模型调用次数：{calls_used}\n"
                    f"模型记录：{json.dumps(extractor.records[calls_before:], ensure_ascii=False)}\n"
                    f"耗时：{elapsed}秒"
                )
            assert response["merged_constraints"]["dialogue_id"] == session_id
            previous = response["merged_constraints"]
            print(
                f"通过 model={model.model_name} dialogue={dialogue_id}:{turn_index} "
                f"elapsed={elapsed}s",
                flush=True,
            )


@pytest.mark.integration
def test_验收模型健康检查(real_models):
    _, backup = real_models

    check_chat_model(backup.chat_model)


@pytest.mark.integration
def test_验收模型完整执行20组29轮且全部首次通过(
    real_models,
    build_service,
    session_factory,
    profile_id,
    seed_ingredients,
    clock_at,
):
    del seed_ingredients
    _, backup = real_models
    _run_dialogues(
        model=backup,
        dialogue_ids=tuple(range(1, 21)),
        service_builder=build_service,
        session_factory=session_factory,
        profile_id=profile_id,
        clock=clock_at(12, 0),
    )


@pytest.mark.integration
def test_验收模型五个重点场景分别使用新会话重复三次(
    real_models,
    build_service,
    session_factory,
    profile_id,
    seed_ingredients,
    clock_at,
):
    del seed_ingredients
    _, backup = real_models
    for _ in range(3):
        _run_dialogues(
            model=backup,
            dialogue_ids=FOCUS_DIALOGUE_IDS,
            service_builder=build_service,
            session_factory=session_factory,
            profile_id=profile_id,
            clock=clock_at(12, 0),
        )
