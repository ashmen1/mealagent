from __future__ import annotations

import copy
import csv
import importlib
import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


SPEC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPEC_DIR.parents[1]
DATA_DIR = REPO_ROOT / "datas" / "processed"

REAL_RECIPE_PATH = DATA_DIR / "Recipes" / "RecipeComplete.json"
REAL_INGREDIENT_PATH = DATA_DIR / "Ingredients" / "Ingredients2Nutrition.csv"
REAL_PROFILE_PATH = DATA_DIR / "users" / "50个用户健康档案_归一化.json"

CSV_FIELDS = [
    "标准食材名",
    "英文名",
    "分类",
    "USDA描述",
    "USDA_FDC_ID",
    "energy_kcal",
    "protein_g",
    "fat_g",
    "carbohydrate_g",
    "fiber_g",
    "sodium_mg",
    "calcium_mg",
    "iron_mg",
    "cholesterol_mg",
    "别名",
]


@dataclass(frozen=True)
class InputPaths:
    recipes: Path
    ingredients: Path
    profiles: Path


def default_recipe() -> dict[str, Any]:
    return {
        "name": "测试菜品",
        "ingredients": {"测试食材": "5g"},
        "total_time_lower_bound_minutes": 10,
        "atomic_steps": [
            {
                "atom_id": "a1",
                "source_step_index": 0,
                "text": "加入测试食材",
                "duration_expression": None,
                "source_span": "加入测试食材",
            }
        ],
        "labels": ["家常"],
        "fuzzy_quantity_estimates": [],
    }


def default_ingredient(name: str = "测试食材") -> dict[str, Any]:
    return {
        "标准食材名": name,
        "英文名": "Test ingredient",
        "分类": "测试分类",
        "USDA描述": "Test ingredient",
        "USDA_FDC_ID": "100001",
        "energy_kcal": "100.5",
        "protein_g": "10.1",
        "fat_g": "2.5",
        "carbohydrate_g": "12.3",
        "fiber_g": "1.2",
        "sodium_mg": "8.4",
        "calcium_mg": "20.0",
        "iron_mg": "0.8",
        "cholesterol_mg": "0",
        "别名": "测试别名一; 测试别名二",
    }


def default_profile(profile_id: int = 9001) -> dict[str, Any]:
    return {
        "id": profile_id,
        "性别": "女",
        "年龄": 30,
        "劳动强度": "中",
        "特殊人群": [],
        "孕周期": None,
        "口味偏好": "清淡",
        "过敏食材": [],
        "健康需求": [],
        "身高_cm": 165.0,
        "体重_kg": 55.0,
        "BMI": 20.2,
        "体检指标": {},
    }


class InputFactory:
    def __init__(self, root: Path):
        self.root = root
        self.sequence = 0

    def create(
        self,
        *,
        recipes: list[dict[str, Any]] | None = None,
        ingredients: list[dict[str, Any]] | None = None,
        profiles: list[dict[str, Any]] | None = None,
    ) -> InputPaths:
        self.sequence += 1
        case_dir = self.root / f"case_{self.sequence}"
        case_dir.mkdir()

        recipe_path = case_dir / "RecipeComplete.json"
        ingredient_path = case_dir / "Ingredients2Nutrition.csv"
        profile_path = case_dir / "用户健康档案_归一化.json"

        recipe_payload = copy.deepcopy(
            [default_recipe()] if recipes is None else recipes
        )
        ingredient_payload = copy.deepcopy(
            [default_ingredient()] if ingredients is None else ingredients
        )
        profile_payload = copy.deepcopy(
            [default_profile()] if profiles is None else profiles
        )

        recipe_path.write_text(
            json.dumps(recipe_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        profile_path.write_text(
            json.dumps(profile_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.write_csv(ingredient_path, ingredient_payload)

        return InputPaths(recipe_path, ingredient_path, profile_path)

    @staticmethod
    def write_csv(path: Path, rows: list[dict[str, Any]], fields=None) -> None:
        fieldnames = CSV_FIELDS if fields is None else fields
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)


@pytest.fixture(scope="session", autouse=True)
def add_repo_to_python_path():
    root = str(REPO_ROOT)
    sys.path.insert(0, root)
    yield
    if root in sys.path:
        sys.path.remove(root)


@pytest.fixture(scope="session")
def production_contract(add_repo_to_python_path):
    try:
        importer_module = importlib.import_module(
            "backend.infrastructure.database.importer"
        )
        models_module = importlib.import_module(
            "backend.infrastructure.database.models"
        )
        return SimpleNamespace(
            import_basic_data=importer_module.import_basic_data,
            BasicDataImportError=importer_module.BasicDataImportError,
            BasicDataFormatError=importer_module.BasicDataFormatError,
            BasicDataConflictError=importer_module.BasicDataConflictError,
            BasicDataWriteError=importer_module.BasicDataWriteError,
            importer_module=importer_module,
            Base=models_module.Base,
        )
    except (ModuleNotFoundError, AttributeError) as exc:
        pytest.fail(
            "缺少 Spec_00 约定的生产接口："
            "backend.infrastructure.database.importer 或 "
            "backend.infrastructure.database.models；"
            f"原始错误：{exc}",
            pytrace=False,
        )


@pytest.fixture
def input_factory(tmp_path: Path) -> InputFactory:
    return InputFactory(tmp_path)


@pytest.fixture
def db_engine(production_contract):
    database_url = load_test_database_url()
    engine = create_engine(database_url, pool_pre_ping=True)

    production_contract.Base.metadata.drop_all(engine)
    production_contract.Base.metadata.create_all(engine)
    yield engine
    production_contract.Base.metadata.drop_all(engine)
    engine.dispose()


def load_test_database_url() -> str:
    pyproject_path = REPO_ROOT / "pyproject.toml"
    with pyproject_path.open("rb") as stream:
        project_config = tomllib.load(stream)

    try:
        test_database = project_config["tool"]["mealagent"]["test_database"]
        database_url = test_database["url"]
        required_database = test_database["required_database"]
    except (KeyError, TypeError) as exc:
        raise pytest.UsageError(
            "pyproject.toml 缺少 tool.mealagent.test_database 配置"
        ) from exc

    if not isinstance(database_url, str) or not database_url.strip():
        raise pytest.UsageError("测试数据库 URL 必须是非空字符串")
    if not isinstance(required_database, str) or not required_database.strip():
        raise pytest.UsageError("测试数据库名必须是非空字符串")

    parsed_url = make_url(database_url.strip())
    if not parsed_url.drivername.startswith("postgresql"):
        raise pytest.UsageError("完整 Spec_00 必须使用 PostgreSQL 测试库")
    if parsed_url.database != required_database.strip():
        raise pytest.UsageError(
            f"测试只允许连接 {required_database.strip()}"
        )
    return database_url.strip()


@pytest.fixture
def db_session(db_engine):
    with Session(db_engine) as session:
        yield session
        session.rollback()


@pytest.fixture
def invoke_import(production_contract):
    def invoke(paths: InputPaths, session: Session):
        return production_contract.import_basic_data(
            paths.recipes,
            paths.ingredients,
            paths.profiles,
            session,
        )

    return invoke


@pytest.fixture
def assert_import_error(production_contract, invoke_import):
    def assert_error(paths: InputPaths, session: Session, status_code: int):
        expected_type = {
            400: production_contract.BasicDataFormatError,
            409: production_contract.BasicDataConflictError,
            500: production_contract.BasicDataWriteError,
        }[status_code]
        with pytest.raises(expected_type) as captured:
            invoke_import(paths, session)
        assert isinstance(captured.value, production_contract.BasicDataImportError)
        assert captured.value.status_code == status_code
        return captured.value

    return assert_error


def table_count(session: Session, table_name: str) -> int:
    return session.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar_one()
