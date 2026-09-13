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
                   is_recommendable: true,
                   tags: ["晚餐", "川湘菜", "咸"],
                   total_time_lower_bound_minutes: 15,
                   difficulty: "简单"}),
                   (r2:Recipe {name: "白灼芥蓝", dish_type: "菜",
                   is_recommendable: true,
                   tags: ["晚餐", "粤菜", "清淡"],
                   total_time_lower_bound_minutes: 10,
                   difficulty: "中等"}),
                   (r3:Recipe {name: "粤式上汤面", dish_type: "主食",
                   is_recommendable: true,
                   tags: ["晚餐", "粤菜"],
                   total_time_lower_bound_minutes: 30,
                   difficulty: "复杂"}),
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
            CREATE (i1)-[:part_of {is_staple_component: false}]->(r1),
                   (i2)-[:part_of {is_staple_component: false}]->(r1),
                   (i6)-[:part_of {is_staple_component: false}]->(r1)
            """
        )
        session.run(
            """
            MATCH (r2:Recipe {name: "白灼芥蓝"}), (i3:Ingredient {name: "芥蓝"})
            CREATE (i3)-[:part_of {is_staple_component: false}]->(r2)
            """
        )
        session.run(
            """
            MATCH (r3:Recipe {name: "粤式上汤面"}), (i4:Ingredient {name: "面粉"}),
                  (i6:Ingredient {name: "大葱"})
            CREATE (i4)-[:part_of {is_staple_component: true}]->(r3),
                   (i6)-[:part_of {is_staple_component: false}]->(r3)
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


def _add_staple_recipes(graph) -> None:
    with graph.session() as session:
        session.run(
            """
            CREATE (pizza:Recipe {name: "培根披萨", dish_type: "主食",
                    is_recommendable: true, tags: ["晚餐", "西餐风味"],
                    total_time_lower_bound_minutes: 30, difficulty: "中等"}),
                   (corn_recipe:Recipe {name: "蜜汁烤玉米", dish_type: "主食",
                    is_recommendable: true, tags: ["晚餐", "粤菜"],
                    total_time_lower_bound_minutes: 20, difficulty: "简单"}),
                   (rice:Recipe {name: "红薯米饭", dish_type: "主食",
                    is_recommendable: true, tags: ["晚餐", "粤菜"],
                    total_time_lower_bound_minutes: 25, difficulty: "简单"}),
                   (millet_recipe:Recipe {name: "小米粥", dish_type: "主食",
                    is_recommendable: true, tags: ["晚餐", "粤菜"],
                    total_time_lower_bound_minutes: 25, difficulty: "简单"}),
                   (flour:Ingredient {name: "高筋面粉", category: "粮食",
                    is_core_ingredient: true}),
                   (corn:Ingredient {name: "玉米", category: "粮食",
                    is_core_ingredient: true}),
                   (sweet:Ingredient {name: "红薯", category: "薯类",
                    is_core_ingredient: true}),
                   (raw_rice:Ingredient {name: "大米", category: "粮食",
                    is_core_ingredient: true}),
                   (millet:Ingredient {name: "小米", category: "粮食",
                    is_core_ingredient: true})
            CREATE (flour)-[:part_of {is_staple_component: true}]->(pizza),
                   (corn)-[:part_of {is_staple_component: false}]->(pizza),
                   (corn)-[:part_of {is_staple_component: true}]->(corn_recipe),
                   (raw_rice)-[:part_of {is_staple_component: true}]->(rice),
                   (sweet)-[:part_of {is_staple_component: true}]->(rice),
                   (millet)-[:part_of {is_staple_component: true}]->(millet_recipe)
            """
        )


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
                required_ingredient_groups=[
                    {
                        "match": "all",
                        "items": [
                            {"kind": "ingredient", "value": "鸡蛋"}
                        ],
                    }
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
                required_ingredient_groups=[
                    {
                        "match": "all",
                        "items": [{"kind": "concept", "value": "面"}],
                    }
                ]
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
@pytest.mark.parametrize(
    ("max_difficulty", "expected_names"),
    [
        ("简单", ["番茄炒蛋"]),
        ("中等", ["番茄炒蛋", "白灼芥蓝"]),
        (None, ["番茄炒蛋", "白灼芥蓝", "粤式上汤面"]),
    ],
)
def test_难度上限过滤真实图节点(
    max_difficulty,
    expected_names,
    invoke_integration_filter,
):
    constraints = build_integrated_constraints(
        max_difficulty=max_difficulty
    )

    result = invoke_integration_filter(constraints)

    assert _names(result) == expected_names


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


@pytest.mark.integration
def test_玉米或红薯主食不能返回仅以玉米作配料的披萨(
    invoke_integration_filter,
    graph,
):
    _add_staple_recipes(graph)
    constraints = build_integrated_constraints(
        dishes=[
            build_integrated_dish(
                dish_type="主食",
                required_staple_ingredients={
                    "match": "any",
                    "items": ["玉米", "红薯"],
                },
            )
        ]
    )

    result = invoke_integration_filter(constraints)

    assert set(_names(result)) == {"蜜汁烤玉米", "红薯米饭"}
    assert "培根披萨" not in _names(result)


@pytest.mark.integration
def test_主食来源all要求每个食材都承担主食角色(
    invoke_integration_filter,
    graph,
):
    _add_staple_recipes(graph)
    constraints = build_integrated_constraints(
        dishes=[
            build_integrated_dish(
                dish_type="主食",
                required_staple_ingredients={
                    "match": "all",
                    "items": ["大米", "红薯"],
                },
            )
        ]
    )

    result = invoke_integration_filter(constraints)

    assert _names(result) == ["红薯米饭"]


@pytest.mark.integration
def test_米饭作为正向族词可命中大米主食(
    invoke_integration_filter,
    graph,
):
    _add_staple_recipes(graph)
    constraints = build_integrated_constraints(
        dishes=[
            build_integrated_dish(
                dish_type="主食",
                required_staple_ingredients={
                    "match": "all",
                    "items": ["米饭"],
                },
            )
        ]
    )

    result = invoke_integration_filter(constraints)

    assert _names(result) == ["红薯米饭"]


@pytest.mark.integration
def test_排除米饭剔除含大米共同主食的红薯米饭(
    invoke_integration_filter,
    graph,
):
    _add_staple_recipes(graph)
    constraints = build_integrated_constraints(
        dishes=[
            build_integrated_dish(
                dish_type="主食",
                required_staple_ingredients={
                    "match": "any",
                    "items": ["玉米", "红薯"],
                },
                excluded_staple_ingredients=["米饭"],
            )
        ]
    )

    result = invoke_integration_filter(constraints)

    assert _names(result) == ["蜜汁烤玉米"]
    assert "红薯米饭" not in _names(result)


@pytest.mark.integration
def test_排除玉米不剔除只把玉米当配料的披萨(
    invoke_integration_filter,
    graph,
):
    _add_staple_recipes(graph)
    constraints = build_integrated_constraints(
        dishes=[
            build_integrated_dish(
                dish_type="主食",
                excluded_staple_ingredients=["玉米"],
            )
        ]
    )

    result = invoke_integration_filter(constraints)

    assert "培根披萨" in _names(result)
    assert "蜜汁烤玉米" not in _names(result)


@pytest.mark.integration
def test_普通包含玉米仍命中玉米配料(invoke_integration_filter, graph):
    _add_staple_recipes(graph)
    constraints = build_integrated_constraints(
        dishes=[
            build_integrated_dish(
                dish_type="主食",
                required_ingredient_groups=[
                    {
                        "match": "all",
                        "items": [
                            {"kind": "ingredient", "value": "玉米"}
                        ],
                    }
                ],
            )
        ]
    )

    result = invoke_integration_filter(constraints)

    assert set(_names(result)) == {"培根披萨", "蜜汁烤玉米"}
    with graph.session() as session:
        value = session.run(
            """
            MATCH (:Ingredient {name: '玉米'})-[p:part_of]->
                  (:Recipe {name: '培根披萨'})
            RETURN p.is_staple_component AS value
            """
        ).single()["value"]
    assert value is False


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_value",
    [None, "true"],
    ids=["缺失属性", "非布尔属性"],
)
def test_主食校验候选关系属性非法返回500(
    invalid_value,
    invoke_integration_filter,
    graph,
):
    _add_staple_recipes(graph)
    with graph.session() as session:
        if invalid_value is None:
            session.run(
                """
                MATCH (r:Recipe {name: '蜜汁烤玉米'})
                CREATE (:Ingredient {name: '盐', category: '调味料',
                        is_core_ingredient: false})-[:part_of]->(r)
                """
            )
        else:
            session.run(
                """
                MATCH (:Ingredient {name: '玉米'})-[p:part_of]->
                      (:Recipe {name: '蜜汁烤玉米'})
                SET p.is_staple_component = $invalid_value
                """,
                invalid_value=invalid_value,
            )
    constraints = build_integrated_constraints(
        dishes=[
            build_integrated_dish(
                dish_type="主食",
                required_staple_ingredients={
                    "match": "all",
                    "items": ["玉米"],
                },
            )
        ]
    )

    with pytest.raises(Exception) as captured:
        invoke_integration_filter(constraints)

    assert getattr(captured.value, "status_code", None) == 500


@pytest.mark.integration
def test_其他规则已排除的菜谱不检查缺失主食属性(
    invoke_integration_filter,
    graph,
):
    _add_staple_recipes(graph)
    with graph.session() as session:
        session.run(
            """
            MATCH (:Ingredient {name: '玉米'})-[p:part_of]->
                  (:Recipe {name: '培根披萨'})
            REMOVE p.is_staple_component
            """
        )
    constraints = build_integrated_constraints(
        dishes=[
            build_integrated_dish(
                dish_type="主食",
                cuisines=["粤菜"],
                required_staple_ingredients={
                    "match": "all",
                    "items": ["玉米"],
                },
            )
        ]
    )

    result = invoke_integration_filter(constraints)

    assert _names(result) == ["蜜汁烤玉米"]
