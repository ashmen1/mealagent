from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

import pytest
from decimal import Decimal
from sqlalchemy.orm import sessionmaker

from spec05_support import REPO_ROOT, default_profile


def _load_dotenv() -> None:
    """加载真实LLM配置，但不覆盖调用环境显式设置的值。"""

    for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


@pytest.mark.integration
def test_早餐午餐晚餐贯通真实LLM图筛选候选营养和用户DRI(
    db_session,
    invoke_import,
    input_factory,
    import_contract,
):
    pytest.importorskip("neo4j", reason="跨模块集成测试需要项目完整依赖环境")
    _load_dotenv()

    from backend.infrastructure.graph import create_neo4j_driver
    from backend.infrastructure.llm import (
        create_langchain_constraint_extractor_from_environment,
    )
    from backend.services import (
        ConstraintIntegrationService,
        DialogueConstraintService,
        DishFilteringService,
        NutritionService,
        ProfileConstraintService,
    )

    # 本跨模块用例使用最小可控营养夹具验证服务链路；
    # 正式数据的完整导入由 test_spec05_真实全链路.py 独立验证。
    paths = input_factory.create(profiles=[default_profile(id=44)])
    result = invoke_import(paths, db_session)
    assert result["counts"]["recipe_nutrition"] == 1
    assert result["counts"]["profile_dri_targets"] == 27

    session_factory = sessionmaker(
        bind=db_session.get_bind(),
        expire_on_commit=False,
    )
    with (REPO_ROOT / "pyproject.toml").open("rb") as stream:
        test_neo4j = tomllib.load(stream)["tool"]["mealagent"]["test_neo4j"]
    driver = create_neo4j_driver(
        test_neo4j["uri"],
        test_neo4j["user"],
        test_neo4j["password"],
    )
    try:
        with driver.session() as graph_session:
            graph_session.run("MATCH (n) DETACH DELETE n").consume()
            graph_session.run(
                """
                CREATE (r1:Recipe {name: '早餐测试菜', dish_type: '菜',
                        tags: ['早餐', '清淡'], total_time_lower_bound_minutes: 10}),
                       (r2:Recipe {name: '午餐清爽菜', dish_type: '菜',
                        tags: ['午餐', '清淡'], total_time_lower_bound_minutes: 15}),
                       (r3:Recipe {name: '晚餐快手菜', dish_type: '菜',
                        tags: ['晚餐', '上班族', '清淡'], total_time_lower_bound_minutes: 20}),
                       (i1:Ingredient {name: '测试食材1', category: '测试', is_core_ingredient: true}),
                       (i2:Ingredient {name: '测试食材2', category: '测试', is_core_ingredient: true}),
                       (i3:Ingredient {name: '测试食材3', category: '测试', is_core_ingredient: true}),
                       (i1)-[:part_of]->(r1), (i2)-[:part_of]->(r2), (i3)-[:part_of]->(r3)
                """
            ).consume()
            node_count = graph_session.run(
                "MATCH (n:Recipe) RETURN count(n) AS value"
            ).single()["value"]
        assert node_count == 3

        profile_service = ProfileConstraintService(session_factory)
        dialogue_service = DialogueConstraintService(
            session_factory,
            create_langchain_constraint_extractor_from_environment(),
        )
        integration_service = ConstraintIntegrationService()
        filtering_service = DishFilteringService(driver)
        nutrition_service = NutritionService(session_factory)

        dialogues = json.loads(
            (REPO_ROOT / "datas" / "raw" / "对话用例.json").read_text(
                encoding="utf-8"
            )
        )
        dialogue_by_id = {item["id"]: item for item in dialogues}
        profile_constraints = profile_service.extract(44)

        for dialogue_id, meal_period in ((2, "早餐"), (3, "午餐"), (6, "晚餐")):
            dialogue_constraints = dialogue_service.extract(dialogue_by_id[dialogue_id])
            assert meal_period in dialogue_constraints["meal_periods"]

            integrated = integration_service.integrate(
                profile_constraints,
                dialogue_constraints,
            )
            assert integrated["has_conflicts"] is False

            filtered = filtering_service.filter(integrated)
            candidate_names = [
                candidate["recipe_name"]
                for group in filtered["dishes"]
                for candidate in group
            ]
            assert candidate_names
            selected_names = list(dict.fromkeys(candidate_names))[:5]

            existing_names = {
                row[0]
                for row in db_session.query(import_contract.Recipe.name).all()
            }
            for selected_name in selected_names:
                if selected_name in existing_names:
                    continue
                recipe = import_contract.Recipe(
                    name=selected_name,
                    total_time_lower_bound_minutes=10,
                    dish_type="菜",
                    atomic_steps=[],
                    labels=[meal_period],
                    difficulty="简单",
                )
                db_session.add(recipe)
                db_session.flush()
                db_session.add(
                    import_contract.RecipeNutrition(
                        recipe_id=recipe.id,
                        energy_kcal=Decimal("100.00"),
                        protein_g=Decimal("10.00"),
                        fat_g=Decimal("5.00"),
                        carbohydrate_g=Decimal("15.00"),
                        fiber_g=Decimal("2.00"),
                        sodium_mg=Decimal("100.00"),
                        calcium_mg=Decimal("20.00"),
                        iron_mg=Decimal("1.00"),
                        cholesterol_mg=Decimal("0.00"),
                    )
                )
                existing_names.add(selected_name)
            db_session.commit()

            recipe_nutrition = nutrition_service.get_recipe_nutrition(selected_names)
            assert [item["recipe_name"] for item in recipe_nutrition] == selected_names
            assert all(item["energy_kcal"] >= 0 for item in recipe_nutrition)

            meal_targets = nutrition_service.get_meal_nutrition_targets(44, meal_period)
            assert meal_targets["meal_period"] == meal_period
            assert len(meal_targets["nutrients"]) == 9
            assert meal_targets["nutrients"]["energy_kcal"]["target_value"] > 0
            assert meal_targets["nutrients"]["cholesterol_mg"]["status"] == "not_established"
    finally:
        driver.close()
