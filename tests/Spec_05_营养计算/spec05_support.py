from __future__ import annotations

import copy
import csv
import importlib
import json
import sys
import tomllib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


SPEC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPEC_DIR.parents[1]
repo_root_text = str(REPO_ROOT)
if repo_root_text not in sys.path:
    sys.path.insert(0, repo_root_text)


NUTRIENT_FIELDS = (
    "energy_kcal",
    "protein_g",
    "fat_g",
    "carbohydrate_g",
    "fiber_g",
    "sodium_mg",
    "calcium_mg",
    "iron_mg",
    "cholesterol_mg",
)

INGREDIENT_FIELDS = (
    "标准食材名",
    "英文名",
    "分类",
    "USDA描述",
    "USDA_FDC_ID",
    *NUTRIENT_FIELDS,
    "别名",
)

DRI_FIELDS = (
    "性别",
    "年龄下限",
    "年龄上限",
    "生理阶段",
    "劳动强度",
    "energy_mj",
    "protein_rni_g",
    "protein_amdr_min_percent",
    "protein_amdr_max_percent",
    "fat_amdr_min_percent",
    "fat_amdr_max_percent",
    "carbohydrate_amdr_min_percent",
    "carbohydrate_amdr_max_percent",
    "fiber_ai_min_g",
    "fiber_ai_max_g",
    "sodium_ai_mg",
    "sodium_pi_mg",
    "calcium_rni_mg",
    "calcium_ul_mg",
    "iron_rni_mg",
    "iron_ul_mg",
)


@dataclass(frozen=True)
class InputPaths:
    recipes: Path
    ingredients: Path
    profiles: Path
    dri: Path


def quantity_resolution(
    quantity_text: str,
    grams: str | int | float,
    *,
    is_estimated: bool = False,
    is_excluded: bool = False,
    calculation_path: str | None = None,
    reference_source: str = "测试证据#quantity",
) -> dict[str, Any]:
    grams_value = Decimal(str(grams))
    distribution = (
        {
            "sample_count": 0,
            "min_g": None,
            "p25_g": None,
            "median_g": None,
            "p75_g": None,
            "max_g": None,
            "mean_g": None,
            "common_values": [],
            "method": "nutrition_excluded",
        }
        if is_excluded
        else {
            "sample_count": 1,
            "min_g": float(grams_value),
            "p25_g": float(grams_value),
            "median_g": float(grams_value),
            "p75_g": float(grams_value),
            "max_g": float(grams_value),
            "mean_g": float(grams_value),
            "common_values": [{"grams": float(grams_value), "count": 1}],
            "method": "nearest_rank_from_final_recipe_ingredient_weights",
        }
    )
    return {
        "original_quantity": quantity_text,
        "resolved_quantity_g": float(grams_value),
        "is_quantity_estimated": is_estimated,
        "is_nutrition_excluded": is_excluded,
        "calculation_path": calculation_path or f"原始{quantity_text} → 测试取值 → {grams_value}g",
        "reference_source": reference_source,
        "ingredient_weight_distribution": distribution,
    }


def _infer_resolution(ingredient_name: str, quantity_text: str) -> dict[str, Any]:
    if ingredient_name in {"水", "清水", "温水", "温开水", "热水", "凉开水", "冰水", "冷水", "纯净水", "开水"}:
        return quantity_resolution(
            quantity_text,
            0,
            is_excluded=True,
            reference_source="Spec05纯水排除规则",
        )
    normalized = quantity_text.strip().lower()
    for suffix, factor in (("kg", Decimal("1000")), ("千克", Decimal("1000")), ("公斤", Decimal("1000")), ("斤", Decimal("500")), ("g", Decimal("1")), ("克", Decimal("1"))):
        if normalized.endswith(suffix):
            raw_number = normalized[: -len(suffix)].strip()
            try:
                grams = Decimal(raw_number) * factor
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"测试菜谱必须显式提供最终克重：{ingredient_name}/{quantity_text}") from exc
            return quantity_resolution(quantity_text, grams)
    raise ValueError(f"测试菜谱必须显式提供最终克重：{ingredient_name}/{quantity_text}")


def default_recipe(
    *,
    name: str = "测试菜品",
    quantity_text: str = "10g",
    ingredient_name: str = "测试食材",
    resolved_grams: str | int | float | None = None,
    is_estimated: bool = False,
) -> dict[str, Any]:
    resolution = (
        quantity_resolution(
            quantity_text,
            resolved_grams,
            is_estimated=is_estimated,
            is_excluded=ingredient_name in {"水", "清水", "温水", "温开水", "热水", "凉开水", "冰水", "冷水", "纯净水", "开水"},
        )
        if resolved_grams is not None
        else _infer_resolution(ingredient_name, quantity_text)
    )
    return {
        "name": name,
        "ingredients": {ingredient_name: quantity_text},
        "total_time_lower_bound_minutes": 10,
        "dish_type": "菜",
        "atomic_steps": [],
        "labels": ["午餐"],
        "fuzzy_quantity_estimates": [],
        "ingredient_quantity_resolutions": {ingredient_name: resolution},
    }


def default_ingredient(name: str = "测试食材", **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "标准食材名": name,
        "英文名": "Test ingredient",
        "分类": "测试分类",
        "USDA描述": "Test ingredient, audited",
        "USDA_FDC_ID": "100001",
        "energy_kcal": "100.55",
        "protein_g": "10.11",
        "fat_g": "2.55",
        "carbohydrate_g": "12.34",
        "fiber_g": "1.23",
        "sodium_mg": "8.45",
        "calcium_mg": "20.05",
        "iron_mg": "0.85",
        "cholesterol_mg": "0",
        "别名": "",
    }
    row.update(overrides)
    return row


def default_profile(**overrides: Any) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "id": 25,
        "性别": "男",
        "年龄": 30,
        "劳动强度": "低",
        "特殊人群": [],
        "孕周期": None,
        "是否有月经": None,
        "口味偏好": "清淡",
        "过敏食材": [],
        "健康需求": [],
        "身高_cm": 170,
        "体重_kg": 65,
        "BMI": 22.49,
        "体检指标": {},
    }
    profile.update(copy.deepcopy(overrides))
    return profile


def default_dri_rule(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "性别": "男",
        "年龄下限": 30,
        "年龄上限": 49,
        "生理阶段": "普通",
        "劳动强度": "低",
        "energy_mj": "8.58",
        "protein_rni_g": "65",
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
        "iron_rni_mg": "12",
        "iron_ul_mg": "42",
    }
    row.update(overrides)
    return row


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

        paths = InputPaths(
            recipes=case_dir / "RecipeComplete.json",
            ingredients=case_dir / "Ingredients2Nutrition.csv",
            profiles=case_dir / "用户健康档案_归一化.json",
            dri=case_dir / "DRI2023.csv",
        )
        recipe_rows = copy.deepcopy([default_recipe()] if recipes is None else recipes)
        for recipe in recipe_rows:
            if "ingredient_quantity_resolutions" not in recipe:
                recipe["ingredient_quantity_resolutions"] = {
                    ingredient_name: _infer_resolution(ingredient_name, quantity_text)
                    for ingredient_name, quantity_text in recipe["ingredients"].items()
                }
        paths.recipes.write_text(
            json.dumps(
                recipe_rows,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        paths.profiles.write_text(
            json.dumps(
                [default_profile()] if profiles is None else profiles,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self._write_csv(
            paths.ingredients,
            INGREDIENT_FIELDS,
            [default_ingredient()] if ingredients is None else ingredients,
        )
        self._write_csv(
            paths.dri,
            DRI_FIELDS,
            [default_dri_rule()] if dri_rules is None else dri_rules,
        )
        return paths

    @staticmethod
    def _write_csv(
        path: Path,
        fieldnames: tuple[str, ...],
        rows: list[dict[str, Any]],
    ) -> None:
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


@pytest.fixture
def input_factory(tmp_path: Path) -> InputFactory:
    return InputFactory(tmp_path)


@pytest.fixture
def import_contract():
    try:
        importer = importlib.import_module("backend.infrastructure.database.importer")
        models = importlib.import_module("backend.infrastructure.database.models")
        return SimpleNamespace(
            import_basic_data=importer.import_basic_data,
            Base=models.Base,
            Recipe=models.Recipe,
            Ingredient=models.Ingredient,
            RecipeIngredient=models.RecipeIngredient,
            UserProfile=models.UserProfile,
            RecipeNutrition=models.RecipeNutrition,
            ProfileDriTarget=models.ProfileDriTarget,
        )
    except (ModuleNotFoundError, AttributeError) as exc:
        pytest.fail(
            "缺少 Spec_05 约定的导入接口或数据模型；"
            f"原始错误：{exc}",
            pytrace=False,
        )


@pytest.fixture
def db_session(import_contract):
    engine = create_engine(load_test_database_url(), pool_pre_ping=True)
    import_contract.Base.metadata.drop_all(engine)
    import_contract.Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
        session.rollback()
    import_contract.Base.metadata.drop_all(engine)
    engine.dispose()


def load_test_database_url() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as stream:
        config = tomllib.load(stream)["tool"]["mealagent"]["test_database"]
    database_url = config["url"]
    required_database = config["required_database"]
    parsed_url = make_url(database_url)
    if not parsed_url.drivername.startswith("postgresql"):
        raise pytest.UsageError("Spec05 集成测试必须使用 PostgreSQL")
    if parsed_url.database != required_database:
        raise pytest.UsageError(f"测试只允许连接 {required_database}")
    return database_url


@pytest.fixture
def invoke_import(import_contract):
    def invoke(paths: InputPaths, session: Session):
        return import_contract.import_basic_data(
            paths.recipes,
            paths.ingredients,
            paths.profiles,
            paths.dri,
            session,
        )

    return invoke


def row_count(session: Session, model: type[Any]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


@pytest.fixture
def service_contract():
    try:
        service_module = importlib.import_module("backend.services.nutrition")
        models = importlib.import_module("backend.infrastructure.database.models")
        return SimpleNamespace(
            NutritionService=service_module.NutritionService,
            Base=models.Base,
            Recipe=models.Recipe,
            RecipeNutrition=models.RecipeNutrition,
            UserProfile=models.UserProfile,
            ProfileDriTarget=models.ProfileDriTarget,
        )
    except (ModuleNotFoundError, AttributeError) as exc:
        pytest.fail(
            "缺少 Spec_05 约定的 NutritionService 或数据模型；"
            f"原始错误：{exc}",
            pytrace=False,
        )


def available_target(
    *,
    target: str | None = None,
    lower: str | None = None,
    upper: str | None = None,
    unit: str,
    target_basis: str | None = None,
    lower_basis: str | None = None,
    upper_basis: str | None = None,
) -> dict[str, Any]:
    return {
        "status": "available",
        "unit": unit,
        "target_value": Decimal(target) if target is not None else None,
        "lower_bound": Decimal(lower) if lower is not None else None,
        "upper_bound": Decimal(upper) if upper is not None else None,
        "target_basis": target_basis,
        "lower_basis": lower_basis,
        "upper_basis": upper_basis,
    }


def lunch_targets() -> dict[str, dict[str, Any]]:
    return {
        "energy_kcal": available_target(
            target="820.25", unit="kcal", target_basis="EER"
        ),
        "protein_g": available_target(
            target="26.00",
            lower="20.51",
            upper="41.01",
            unit="g",
            target_basis="RNI",
            lower_basis="AMDR",
            upper_basis="AMDR",
        ),
        "fat_g": available_target(
            lower="18.23",
            upper="27.34",
            unit="g",
            lower_basis="AMDR",
            upper_basis="AMDR",
        ),
        "carbohydrate_g": available_target(
            lower="102.53",
            upper="133.29",
            unit="g",
            lower_basis="AMDR",
            upper_basis="AMDR",
        ),
        "fiber_g": available_target(
            lower="10.00",
            upper="12.00",
            unit="g",
            lower_basis="AI",
            upper_basis="AI",
        ),
        "sodium_mg": available_target(
            target="600.00",
            upper="800.00",
            unit="mg",
            target_basis="AI",
            upper_basis="PI",
        ),
        "calcium_mg": available_target(
            target="320.00",
            upper="800.00",
            unit="mg",
            target_basis="RNI",
            upper_basis="UL",
        ),
        "iron_mg": available_target(
            target="4.80",
            upper="16.80",
            unit="mg",
            target_basis="RNI",
            upper_basis="UL",
        ),
        "cholesterol_mg": {
            "status": "not_established",
            "unit": "mg",
            "target_value": None,
            "lower_bound": None,
            "upper_bound": None,
            "target_basis": None,
            "lower_basis": None,
            "upper_basis": None,
        },
    }


@pytest.fixture
def service_context(service_contract):
    engine = create_engine(load_test_database_url(), pool_pre_ping=True)
    service_contract.Base.metadata.drop_all(engine)
    service_contract.Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine, expire_on_commit=False)

    with session_factory() as session:
        recipes = [
            service_contract.Recipe(
                id=1,
                name="菜谱甲",
                total_time_lower_bound_minutes=10,
                dish_type="菜",
                atomic_steps=[],
                labels=[],
            ),
            service_contract.Recipe(
                id=2,
                name="菜谱乙",
                total_time_lower_bound_minutes=20,
                dish_type="汤",
                atomic_steps=[],
                labels=[],
            ),
        ]
        session.add_all(recipes)
        session.add_all(
            [
                service_contract.RecipeNutrition(
                    recipe_id=1,
                    energy_kcal=Decimal("10.01"),
                    protein_g=Decimal("1.01"),
                    fat_g=Decimal("2.01"),
                    carbohydrate_g=Decimal("3.01"),
                    fiber_g=Decimal("4.01"),
                    sodium_mg=Decimal("5.01"),
                    calcium_mg=Decimal("6.01"),
                    iron_mg=Decimal("7.01"),
                    cholesterol_mg=Decimal("8.01"),
                ),
                service_contract.RecipeNutrition(
                    recipe_id=2,
                    energy_kcal=Decimal("20.02"),
                    protein_g=Decimal("1.02"),
                    fat_g=Decimal("2.02"),
                    carbohydrate_g=Decimal("3.02"),
                    fiber_g=Decimal("4.02"),
                    sodium_mg=Decimal("5.02"),
                    calcium_mg=Decimal("6.02"),
                    iron_mg=Decimal("7.02"),
                    cholesterol_mg=Decimal("8.02"),
                ),
            ]
        )
        session.add(
            service_contract.UserProfile(
                id=25,
                sex="男",
                age=30,
                activity_level="低",
                special_populations=[],
                gestational_week=None,
                is_menstruating=None,
                taste_preference="清淡",
                allergens=[],
                health_goals=[],
                height_cm=Decimal("170"),
                weight_kg=Decimal("65"),
                bmi=Decimal("22.49"),
                medical_metrics={},
            )
        )
        session.flush()
        for nutrient, values in lunch_targets().items():
            session.add(
                service_contract.ProfileDriTarget(
                    profile_id=25,
                    meal_period="午餐",
                    nutrient=nutrient,
                    **values,
                )
            )
        session.commit()

    yield SimpleNamespace(
        service=service_contract.NutritionService(session_factory),
        session_factory=session_factory,
    )
    service_contract.Base.metadata.drop_all(engine)
    engine.dispose()


def assert_status_code(callable_: Any, expected_status: int) -> Exception:
    with pytest.raises(Exception) as captured:
        callable_()
    assert getattr(captured.value, "status_code", None) == expected_status
    return captured.value
