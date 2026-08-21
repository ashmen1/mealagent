from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
repo_root_text = str(REPO_ROOT)
if repo_root_text not in sys.path:
    sys.path.insert(0, repo_root_text)

from backend.application import create_constraint_services
from tests.graph_data_support import ensure_graph_data
from backend.core.dish_filtering_contract import ALLERGEN_CONCEPT_MEMBERS
from backend.services import ConstraintIntegrationService

DIALOGUES_PATH = REPO_ROOT / "datas" / "raw" / "对话用例.json"
USERS_PATH = (
    REPO_ROOT / "datas" / "processed" / "users"
    / "50个用户健康档案_归一化.json"
)
RANDOM_SEED = 42
SAMPLE_SIZE = 5

LLM_ENVIRONMENT_NAMES = frozenset(
    {
        "LLM_PROVIDER",
        "LLM_BASE_URL",
        "LLM_AUTH_TOKEN",
        "LLM_MODEL",
        "LLM_PROVIDER_BACKUP",
        "LLM_BASE_URL_BACKUP",
        "LLM_AUTH_TOKEN_BACKUP",
        "LLM_MODEL_BACKUP",
    }
)


def _load_dotenv() -> None:
    """加载环境；LLM配置以.env为准，其他配置保留进程优先级。"""

    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        raise AssertionError("端到端集成测试需要仓库根目录下的.env")
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        normalized_name = name.strip()
        normalized_value = value.strip()
        if normalized_name in LLM_ENVIRONMENT_NAMES:
            os.environ[normalized_name] = normalized_value
        else:
            os.environ.setdefault(normalized_name, normalized_value)


@pytest.fixture(scope="module")
def services():
    """真实应用容器：PG session_factory + 真实 LLM + Neo4j driver。

    若 Neo4j 图被其他集成测试清空，则先重导真实数据。
    """
    _load_dotenv()
    ensure_graph_data()
    with create_constraint_services() as services:
        yield services


@pytest.fixture(scope="module")
def sample_users() -> list[int]:
    with USERS_PATH.open(encoding="utf-8") as stream:
        users = json.load(stream)
    random.seed(RANDOM_SEED)
    sampled = random.sample(users, SAMPLE_SIZE)
    return [user["id"] for user in sampled]


@pytest.fixture(scope="module")
def single_turn_dialogues() -> list[dict]:
    with DIALOGUES_PATH.open(encoding="utf-8") as stream:
        dialogues = json.load(stream)
    return [d for d in dialogues if d["turn_count"] == 1]


@pytest.fixture(scope="module")
def integration_service():
    return ConstraintIntegrationService()


@pytest.fixture(scope="module")
def dialogue_constraints_by_id(services, single_turn_dialogues):
    """14组对话各建独立会话提取一次并在3条测试间共享，避免重复LLM调用。"""

    extracted = {}
    for dialogue in single_turn_dialogues:
        result = None
        for attempt in range(1, 6):
            try:
                session_id = services.dialogue.create_session(25)
                result = services.dialogue.submit_turn(
                    session_id,
                    dialogue["user_messages"][0],
                )
                break
            except Exception:
                if attempt == 5:
                    raise
        extracted[dialogue["id"]] = result["merged_constraints"]
    return extracted


@pytest.mark.integration
def test_端到端真实数据全链路(
    services,
    integration_service,
    sample_users,
    single_turn_dialogues,
    dialogue_constraints_by_id,
):
    """随机5用户 × 14条单轮对话：档案→对话→整合→Neo4j过滤全链路。"""
    results = []
    for profile_id in sample_users:
        for dialogue in single_turn_dialogues:
            profile_constraints = services.profile.extract(profile_id)
            dialogue_constraints = dialogue_constraints_by_id[dialogue["id"]]
            integrated = integration_service.integrate(
                profile_constraints,
                dialogue_constraints,
            )
            if integrated["has_conflicts"]:
                continue
            filtering_result = services.dish_filtering.filter(integrated)

            for matches in filtering_result["dishes"]:
                assert isinstance(matches, list)
                for match in matches:
                    assert match["recipe_name"]
                    assert isinstance(match["matched_tags"], list)
                    assert isinstance(match["matched_groups"], list)
            results.append(
                {
                    "profile_id": profile_id,
                    "dialogue_id": dialogue["id"],
                    "user_message": dialogue["user_messages"][0],
                    "dish_count": len(integrated["dishes"]),
                    "unmatched_allergens": filtering_result[
                        "unmatched_allergens"
                    ],
                    "candidates_per_group": [
                        len(matches)
                        for matches in filtering_result["dishes"]
                    ],
                }
            )
    assert len(results) == len(sample_users) * len(single_turn_dialogues)


@pytest.mark.integration
def test_端到端候选非空比例(
    services,
    integration_service,
    sample_users,
    single_turn_dialogues,
    dialogue_constraints_by_id,
):
    """大多数组合应能过滤出候选；空候选组合允许但比例受限。"""
    total = 0
    empty_groups = 0
    for profile_id in sample_users:
        profile_constraints = services.profile.extract(profile_id)
        for dialogue in single_turn_dialogues:
            dialogue_constraints = dialogue_constraints_by_id[dialogue["id"]]
            integrated = integration_service.integrate(
                profile_constraints,
                dialogue_constraints,
            )
            if integrated["has_conflicts"]:
                continue
            filtering_result = services.dish_filtering.filter(integrated)
            for matches in filtering_result["dishes"]:
                total += 1
                if not matches:
                    empty_groups += 1
    # 空候选比例应低于一半（过滤语义正常）
    assert empty_groups / total < 0.5


@pytest.mark.integration
def test_端到端过敏词被排除(
    services,
    integration_service,
    sample_users,
    single_turn_dialogues,
    dialogue_constraints_by_id,
):
    """含过敏档案的组合：候选菜谱的食材不得含任何过敏成员。"""
    for profile_id in sample_users:
        profile_constraints = services.profile.extract(profile_id)
        if not profile_constraints["allergens"]:
            continue
        dialogue_constraints = dialogue_constraints_by_id[
            single_turn_dialogues[0]["id"]
        ]
        integrated = integration_service.integrate(
            profile_constraints,
            dialogue_constraints,
        )
        if integrated["has_conflicts"]:
            continue
        filtering_result = services.dish_filtering.filter(integrated)
        for matches in filtering_result["dishes"]:
            for match in matches:
                # 真实校验：候选菜的食材不含任何过敏成员
                for allergen in profile_constraints["allergens"]:
                    members = ALLERGEN_CONCEPT_MEMBERS.get(allergen, ())
                    if not members:
                        continue
                    ingredients = _load_recipe_ingredients(
                        services, match["recipe_name"]
                    )
                    excluded = set(members)
                    assert not excluded.intersection(ingredients), (
                        f"候选菜 {match['recipe_name']} 含过敏食材 "
                        f"{excluded.intersection(ingredients)}"
                    )


def _load_recipe_ingredients(services, recipe_name: str) -> set[str]:
    """从 PG 查该菜谱的全部食材名。"""
    session_factory = services.profile._session_factory
    from sqlalchemy import select

    from backend.infrastructure.database.models import (
        Ingredient,
        Recipe,
        RecipeIngredient,
    )

    with session_factory() as session:
        recipe_id = session.scalar(
            select(Recipe.id).where(Recipe.name == recipe_name)
        )
        if recipe_id is None:
            return set()
        rows = session.execute(
            select(Ingredient.name)
            .join(RecipeIngredient, RecipeIngredient.ingredient_id == Ingredient.id)
            .where(RecipeIngredient.recipe_id == recipe_id)
        )
        return {row[0] for row in rows}
