from __future__ import annotations

import copy
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest


SPEC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPEC_DIR.parents[1]
repo_root_text = str(REPO_ROOT)
if repo_root_text not in sys.path:
    sys.path.insert(0, repo_root_text)


def build_integrated_constraints(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "profile_id": 25,
        "dialogue_id": 8,
        "meal_periods": [],
        "diner_count": None,
        "total_dish_count": None,
        "max_total_time_minutes": None,
        "max_difficulty": None,
        "available_ingredients": [],
        "allergens": [],
        "dishes": [build_integrated_dish()],
        "has_conflicts": False,
        "conflicts": [],
    }
    values.update(copy.deepcopy(overrides))
    return values


def build_integrated_dish(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "count": None,
        "dish_type": "未指定",
        "taste_preferences": {},
        "cuisines": [],
        "effects": [],
        "special_populations": [],
        "required_ingredient_groups": [],
    }
    values.update(copy.deepcopy(overrides))
    return values


def build_recipe_match(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "recipe_name": "番茄炒蛋",
        "recipe_type": None,
        "matched_tags": [],
        "matched_groups": [],
    }
    values.update(copy.deepcopy(overrides))
    return values


class FakeRecord:
    """模拟 Neo4j 查询返回的 record，按键取值。"""

    def __init__(self, values: dict[str, Any]) -> None:
        self._values = dict(values)

    def __getitem__(self, key: str) -> Any:
        return self._values[key]


class FakeNeo4jSession:
    """模拟 Neo4j session，run() 返回预设 records。"""

    def __init__(self, driver: FakeNeo4jDriver) -> None:
        self._driver = driver

    def run(self, query: str, **params: Any) -> FakeNeo4jResult:
        if self._driver.fail_query:
            raise RuntimeError("Neo4j 不可达")
        self._driver.executed_queries.append((query, params))
        if "AS ingredient_name" in query:
            records = [
                {"ingredient_name": name}
                for name in params["ingredient_names"]
                if name in self._driver.ingredient_names
            ]
            return FakeNeo4jResult([FakeRecord(record) for record in records])
        return FakeNeo4jResult(self._driver._pop_records())

    def close(self) -> None:
        pass

    def __enter__(self) -> FakeNeo4jSession:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class FakeNeo4jResult:
    """模拟 session.run 的返回结果。"""

    def __init__(self, records: list[FakeRecord]) -> None:
        self._records = records

    def single(self) -> FakeRecord | None:
        return self._records[0] if self._records else None

    def __iter__(self) -> FakeNeo4jResult:
        self._index = 0
        return self

    def __next__(self) -> FakeRecord:
        if self._index >= len(self._records):
            raise StopIteration
        record = self._records[self._index]
        self._index += 1
        return record


class FakeNeo4jDriver:
    """记录 session.run 的查询与参数；records 队列按查询次数依次弹出。"""

    def __init__(self) -> None:
        self.executed_queries: list[tuple[str, dict[str, Any]]] = []
        self.ingredient_names = {
            "花生",
            "鸡蛋",
            "芒果",
            "牛奶",
            "啤酒",
            "虾",
        }
        self._record_batches: list[list[dict[str, Any]]] = []
        self._populated = False
        self.fail_query = False

    @property
    def records(self) -> list[dict[str, Any]]:
        raise AttributeError(
            "FakeNeo4jDriver.records 是队列式赋值，请用 "
            "set_records_once/append_records"
        )

    @records.setter
    def records(self, value: list[dict[str, Any]]) -> None:
        """赋值即设定为所有查询返回的同一批记录。"""
        self._record_batches = [value]
        self._populated = True

    def set_records_by_query(self, batches: list[list[dict[str, Any]]]) -> None:
        """按查询顺序依次返回不同批记录。"""
        self._record_batches = batches
        self._populated = True

    def _pop_records(self) -> list[FakeRecord]:
        if self._populated and len(self._record_batches) == 1:
            return [FakeRecord(r) for r in self._record_batches[0]]
        if self._record_batches:
            return [FakeRecord(r) for r in self._record_batches.pop(0)]
        return []

    def session(self) -> FakeNeo4jSession:
        return FakeNeo4jSession(self)

    def close(self) -> None:
        pass


@pytest.fixture
def production_contract():
    try:
        module = importlib.import_module("backend.services.dish_filtering")
        service_cls = module.DishFilteringService
        validation_error = module.DishFilteringValidationError
        execution_error = module.DishFilteringExecutionError
    except (ModuleNotFoundError, AttributeError) as exc:
        pytest.fail(
            "缺少 Spec_04 约定的生产接口："
            "backend.services.dish_filtering.DishFilteringService 及其异常；"
            f"原始错误：{exc}",
            pytrace=False,
        )
    return SimpleNamespace(
        DishFilteringService=service_cls,
        DishFilteringValidationError=validation_error,
        DishFilteringExecutionError=execution_error,
    )


@pytest.fixture
def fake_driver() -> FakeNeo4jDriver:
    return FakeNeo4jDriver()


@pytest.fixture
def invoke_filter(production_contract) -> Callable[..., dict[str, Any]]:
    def invoke(
        constraints: dict[str, Any],
        fake_driver: FakeNeo4jDriver,
    ) -> dict[str, Any]:
        service = production_contract.DishFilteringService(fake_driver)
        return service.filter(constraints)

    return invoke


@pytest.fixture
def assert_filter_error(production_contract, invoke_filter):
    def assert_error(
        constraints: dict[str, Any],
        fake_driver: FakeNeo4jDriver,
        expected_status: int = 400,
    ) -> Exception:
        with pytest.raises(Exception) as captured:
            invoke_filter(constraints, fake_driver)
        assert getattr(captured.value, "status_code", None) == expected_status
        return captured.value

    return assert_error
