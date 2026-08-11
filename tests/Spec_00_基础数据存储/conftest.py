from __future__ import annotations

import copy
import csv
import importlib
import json
import re
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
REAL_DRI_PATH = DATA_DIR / "Nutrition" / "DRI2023.csv"

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
DRI_FIELDS = [
    "性别", "年龄下限", "年龄上限", "生理阶段", "劳动强度", "energy_mj",
    "protein_rni_g", "protein_amdr_min_percent", "protein_amdr_max_percent",
    "fat_amdr_min_percent", "fat_amdr_max_percent",
    "carbohydrate_amdr_min_percent", "carbohydrate_amdr_max_percent",
    "fiber_ai_min_g", "fiber_ai_max_g", "sodium_ai_mg", "sodium_pi_mg",
    "calcium_rni_mg", "calcium_ul_mg", "iron_rni_mg", "iron_ul_mg",
]


@dataclass(frozen=True)
class InputPaths:
    recipes: Path
    ingredients: Path
    profiles: Path
    dri: Path


def default_recipe() -> dict[str, Any]:
    return {
        "name": "测试菜品",
        "ingredients": {"测试食材": "5g"},
        "total_time_lower_bound_minutes": 10,
        "dish_type": "菜",
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
        "ingredient_quantity_resolutions": {
            "测试食材": {
                "original_quantity": "5g",
                "resolved_quantity_g": 5,
                "is_quantity_estimated": False,
                "is_nutrition_excluded": False,
                "calculation_path": "原始5g → 明确质量 → 5.00g",
                "reference_source": "RecipeComplete.json#测试菜品/测试食材",
                "ingredient_weight_distribution": {
                    "sample_count": 1,
                    "min_g": 5,
                    "p25_g": 5,
                    "median_g": 5,
                    "p75_g": 5,
                    "max_g": 5,
                    "mean_g": 5,
                    "common_values": [{"grams": 5, "count": 1}],
                    "method": "nearest_rank_from_final_recipe_ingredient_weights",
                },
            }
        },
    }


def _build_test_resolution(quantity_text: Any) -> dict[str, Any]:
    text_value = quantity_text if isinstance(quantity_text, str) else ""
    exact = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(g|克)\s*", text_value)
    range_match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)\s*[-~～]\s*(\d+(?:\.\d+)?)\s*(g|克)\s*",
        text_value,
    )
    if exact:
        grams = float(exact.group(1))
        is_estimated = False
    elif range_match:
        grams = (float(range_match.group(1)) + float(range_match.group(2))) / 2
        is_estimated = True
    else:
        grams = 10.0
        is_estimated = True
    return {
        "original_quantity": text_value,
        "resolved_quantity_g": grams,
        "is_quantity_estimated": is_estimated,
        "is_nutrition_excluded": False,
        "calculation_path": f"原始{text_value} → 测试最终克重 → {grams:.2f}g",
        "reference_source": "Spec00测试夹具",
        "ingredient_weight_distribution": {
            "sample_count": 1,
            "min_g": grams,
            "p25_g": grams,
            "median_g": grams,
            "p75_g": grams,
            "max_g": grams,
            "mean_g": grams,
            "common_values": [{"grams": grams, "count": 1}],
            "method": "nearest_rank_from_final_recipe_ingredient_weights",
        },
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
        "是否有月经": None,
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
        dri_rules: list[dict[str, Any]] | None = None,
    ) -> InputPaths:
        self.sequence += 1
        case_dir = self.root / f"case_{self.sequence}"
        case_dir.mkdir()

        recipe_path = case_dir / "RecipeComplete.json"
        ingredient_path = case_dir / "Ingredients2Nutrition.csv"
        profile_path = case_dir / "用户健康档案_归一化.json"
        dri_path = case_dir / "DRI2023.csv"

        recipe_payload = copy.deepcopy(
            [default_recipe()] if recipes is None else recipes
        )
        for recipe in recipe_payload:
            ingredients_value = recipe.get("ingredients")
            if isinstance(ingredients_value, dict):
                recipe["ingredient_quantity_resolutions"] = {
                    ingredient_name: _build_test_resolution(quantity_text)
                    for ingredient_name, quantity_text in ingredients_value.items()
                }
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
        self.write_csv(
            dri_path,
            _build_dri_rules(profile_payload) if dri_rules is None else dri_rules,
            DRI_FIELDS,
        )

        return InputPaths(
            recipe_path,
            ingredient_path,
            profile_path,
            dri_path,
        )

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
            paths.dri,
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


def _build_dri_rules(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rules: dict[tuple[Any, ...], dict[str, Any]] = {}
    for profile in profiles:
        sex = profile.get("性别", "女")
        age = profile.get("年龄", 30)
        activity = profile.get("劳动强度", "中")
        populations = profile.get("特殊人群", [])
        stage = "普通"
        if isinstance(populations, list) and "孕妇" in populations:
            week_value = profile.get("孕周期")
            match = re.fullmatch(r"(\d+)周", week_value) if isinstance(week_value, str) else None
            week = int(match.group(1)) if match else week_value if isinstance(week_value, int) else 1
            stage = "孕早期" if week <= 12 else "孕中期" if week <= 27 else "孕晚期"
        elif isinstance(populations, list) and "哺乳期" in populations:
            stage = "哺乳期"
        elif sex == "女" and isinstance(age, int) and 50 <= age <= 64:
            stage = "普通" if profile.get("是否有月经") else "无月经"
        safe_age = age if isinstance(age, int) and age > 0 else 30
        key = (sex, safe_age, stage, activity)
        rules[key] = {
            "性别": sex,
            "年龄下限": safe_age,
            "年龄上限": safe_age,
            "生理阶段": stage,
            "劳动强度": activity,
            "energy_mj": "8.00",
            "protein_rni_g": "60",
            "protein_amdr_min_percent": "10",
            "protein_amdr_max_percent": "20",
            "fat_amdr_min_percent": "20",
            "fat_amdr_max_percent": "30",
            "carbohydrate_amdr_min_percent": "50",
            "carbohydrate_amdr_max_percent": "65",
            "fiber_ai_min_g": "25",
            "fiber_ai_max_g": "30",
            "sodium_ai_mg": "1500",
            "sodium_pi_mg": "2000",
            "calcium_rni_mg": "800",
            "calcium_ul_mg": "2000",
            "iron_rni_mg": "18",
            "iron_ul_mg": "42",
        }
    return list(rules.values())
