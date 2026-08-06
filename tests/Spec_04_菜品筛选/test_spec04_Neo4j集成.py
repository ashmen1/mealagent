from __future__ import annotations

import importlib
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from spec04_support import (
    build_integrated_constraints,
    build_integrated_dish,
    production_contract,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_test_neo4j_config() -> dict[str, str]:
    pyproject_path = REPO_ROOT / "pyproject.toml"
    with pyproject_path.open("rb") as stream:
        project_config = tomllib.load(stream)
    try:
        test_config = project_config["tool"]["mealagent"]["test_neo4j"]
        return {
            key: test_config[key]
            for key in ("uri", "user", "password")
        }
    except (KeyError, TypeError) as exc:
        raise pytest.UsageError(
            "pyproject.toml 缺少 tool.mealagent.test_neo4j 配置"
        ) from exc


@pytest.fixture(scope="session")
def neo4j_driver():
    try:
        neo4j = importlib.import_module("neo4j")
    except ModuleNotFoundError:
        pytest.skip("未安装 neo4j-driver，跳过 Neo4j 集成测试")
    config = _load_test_neo4j_config()
    driver = neo4j.GraphDatabase.driver(
        config["uri"],
        auth=(config["user"], config["password"]),
    )
    try:
        driver.verify_connectivity()
    except Exception:
        driver.close()
        pytest.skip("Neo4j 测试实例不可达，跳过集成测试")
    yield driver
    driver.close()


@pytest.fixture
def graph(neo4j_driver):
    """清空并导入小图；测试间相互隔离。"""
    with neo4j_driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")
        session.run(
            """
            CREATE (r1:Recipe {name: "番茄炒蛋", dish_type: "菜",
                   tags: ["晚餐", "川湘菜", "咸"],
                   total_time_lower_bound_minutes: 15}),
                   (r2:Recipe {name: "白灼芥蓝", dish_type: "菜",
                   tags: ["晚餐", "粤菜", "清淡"],
                   total_time_lower_bound_minutes: 10}),
                   (r3:Recipe {name: "粤式上汤面", dish_type: "主食",
                   tags: ["晚餐", "粤菜"],
                   total_time_lower_bound_minutes: 30}),
                   (i1:Ingredient {name: "番茄", category: "蔬菜",
                   is_core_ingredient: true}),
                   (i2:Ingredient {name: "鸡蛋", category: "蛋奶",
                   is_core_ingredient: true}),
                   (i3:Ingredient {name: "芥蓝", category: "蔬菜",
                   is_core_ingredient: true}),
                   (i4:Ingredient {name: "面粉", category: "粮食",
                   is_core_ingredient: true}),
                   (i5:Ingredient {name: "虾", category: "水产",
                   is_core_ingredient: true}),
                   (i6:Ingredient {name: "大葱", category: "蔬菜",
                   is_core_ingredient: false}),
                   (c1:Concept {name: "海鲜", kind: "allergen"}),
                   (c2:Concept {name: "面", kind: "concept"})
            """
        )
        session.run(
            """
            MATCH (r1:Recipe {name: "番茄炒蛋"}), (i1:Ingredient {name: "番茄"}),
                  (i2:Ingredient {name: "鸡蛋"}), (i6:Ingredient {name: "大葱"})
            CREATE (i1)-[:part_of]->(r1), (i2)-[:part_of]->(r1),
                   (i6)-[:part_of]->(r1)
            """
        )
        session.run(
            """
            MATCH (r2:Recipe {name: "白灼芥蓝"}), (i3:Ingredient {name: "芥蓝"})
            CREATE (i3)-[:part_of]->(r2)
            """
        )
        session.run(
            """
            MATCH (r3:Recipe {name: "粤式上汤面"}), (i4:Ingredient {name: "面粉"}),
                  (i6:Ingredient {name: "大葱"})
            CREATE (i4)-[:part_of]->(r3), (i6)-[:part_of]->(r3)
            """
        )
        session.run(
            """
            MATCH (i5:Ingredient {name: "虾"}), (c1:Concept {name: "海鲜"})
            CREATE (i5)-[:is_a]->(c1)
            """
        )
        session.run(
            """
            MATCH (i4:Ingredient {name: "面粉"}), (c2:Concept {name: "面"})
            CREATE (i4)-[:is_a]->(c2)
            """
        )
    return neo4j_driver


@pytest.fixture
def invoke_integration_filter(production_contract, graph):
    def invoke(constraints: dict[str, Any]) -> dict[str, Any]:
        service = production_contract.DishFilteringService(graph)
        return service.filter(constraints)

    return invoke


def _names(result: dict[str, Any], group_index: int = 0) -> list[str]:
    return [r["recipe_name"] for r in result["dishes"][group_index]]


@pytest.mark.integration
def test_dish_type维度过滤(invoke_integration_filter):
    constraints = build_integrated_constraints(
        meal_periods=["晚餐"],
        dishes=[
            build_integrated_dish(dish_type="菜", cuisines=["粤菜"]),
            build_integrated_dish(dish_type="主食", cuisines=["粤菜"]),
        ],
    )
    result = invoke_integration_filter(constraints)
    # 菜组：白灼芥蓝（菜）；主食组：粤式上汤面（主食）
    assert _names(result, 0) == ["白灼芥蓝"]
    assert _names(result, 1) == ["粤式上汤面"]


@pytest.mark.integration
def test_未指定dish_type不过滤(invoke_integration_filter):
    constraints = build_integrated_constraints(
        meal_periods=["晚餐"],
        dishes=[build_integrated_dish(dish_type="未指定")],
    )
    result = invoke_integration_filter(constraints)
    assert _names(result) == ["番茄炒蛋", "白灼芥蓝", "粤式上汤面"]


@pytest.mark.integration
def test_空约束返回全部候选(invoke_integration_filter):
    result = invoke_integration_filter(build_integrated_constraints())
    assert _names(result) == ["番茄炒蛋", "白灼芥蓝", "粤式上汤面"]


@pytest.mark.integration
def test_餐次与菜系标签过滤(invoke_integration_filter):
    constraints = build_integrated_constraints(
        meal_periods=["晚餐"],
        dishes=[build_integrated_dish(cuisines=["粤菜"])],
    )
    result = invoke_integration_filter(constraints)
    assert _names(result) == ["白灼芥蓝", "粤式上汤面"]


@pytest.mark.integration
def test_口味正向全部命中且否定硬排除(invoke_integration_filter):
    constraints = build_integrated_constraints(
        dishes=[
            build_integrated_dish(
                taste_preferences={"is_light": True, "is_spicy": False}
            )
        ]
    )
    result = invoke_integration_filter(constraints)
    # 白灼芥蓝清淡；番茄炒蛋咸不辣但不清淡 → 仅白灼芥蓝
    assert _names(result) == ["白灼芥蓝"]


@pytest.mark.integration
def test_必需食材ingredient与category(invoke_integration_filter):
    constraints = build_integrated_constraints(
        dishes=[
            build_integrated_dish(
                required_ingredients=[
                    {"kind": "ingredient", "value": "鸡蛋"}
                ]
            )
        ]
    )
    result = invoke_integration_filter(constraints)
    assert _names(result) == ["番茄炒蛋"]


@pytest.mark.integration
def test_必需食材concept命中面(invoke_integration_filter):
    constraints = build_integrated_constraints(
        dishes=[
            build_integrated_dish(
                required_ingredients=[{"kind": "concept", "value": "面"}]
            )
        ]
    )
    result = invoke_integration_filter(constraints)
    assert _names(result) == ["粤式上汤面"]


@pytest.mark.integration
def test_过敏概念经is_a路径排除(invoke_integration_filter):
    constraints = build_integrated_constraints(allergens=["海鲜"])
    result = invoke_integration_filter(constraints)
    # 海鲜过敏排除含虾的菜；图中无含虾的菜，全部保留
    assert _names(result) == ["番茄炒蛋", "白灼芥蓝", "粤式上汤面"]


@pytest.mark.integration
def test_可用食材核心全在辅料不限(invoke_integration_filter):
    constraints = build_integrated_constraints(
        available_ingredients=["番茄", "鸡蛋"]
    )
    result = invoke_integration_filter(constraints)
    # 番茄炒蛋核心食材{番茄,鸡蛋}均在可用列表；白灼芥蓝(芥蓝)、粤式上汤面(面粉)不在
    assert _names(result) == ["番茄炒蛋"]


@pytest.mark.integration
def test_最长时间上限过滤(invoke_integration_filter):
    constraints = build_integrated_constraints(max_total_time_minutes=20)
    result = invoke_integration_filter(constraints)
    # 番茄炒蛋15、白灼芥蓝10；粤式上汤面30超限
    assert _names(result) == ["番茄炒蛋", "白灼芥蓝"]


@pytest.mark.integration
def test_unmatched过敏词进报告不排除(invoke_integration_filter):
    constraints = build_integrated_constraints(allergens=["贝壳类"])
    result = invoke_integration_filter(constraints)
    assert result["unmatched_allergens"] == ["贝壳类"]
    assert _names(result) == ["番茄炒蛋", "白灼芥蓝", "粤式上汤面"]


@pytest.mark.integration
def test_候选按命中标签数降序(invoke_integration_filter):
    constraints = build_integrated_constraints(
        meal_periods=["晚餐"],
        dishes=[build_integrated_dish(cuisines=["粤菜"])],
    )
    result = invoke_integration_filter(constraints)
    matches = result["dishes"][0]
    assert matches[0]["recipe_name"] == "白灼芥蓝"  # 命中3标签
    assert matches[1]["recipe_name"] == "粤式上汤面"  # 命中2标签
