from __future__ import annotations

from functools import partial

from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from backend.infrastructure.database.models import (
    Ingredient,
    ProfileDriTarget,
    Recipe,
    RecipeIngredient,
    RecipeNutrition,
    UserProfile,
)
from backend.infrastructure.llm.langchain_constraints import (
    ConfiguredChatModel,
    create_health_chat_models_from_environment,
)
from backend.services.health_check import HealthCheckService, LlmHealthTarget


def create_health_check_service(
    engine: Engine,
    neo4j_driver: object,
) -> HealthCheckService:
    """使用应用基础设施及独立LLM客户端创建完整健康检查服务。"""

    primary_model, backup_model = create_health_chat_models_from_environment()
    return HealthCheckService(
        postgresql_probe=partial(check_postgresql_connectivity, engine),
        postgresql_data_probe=partial(check_postgresql_data, engine),
        neo4j_probe=partial(check_neo4j_connectivity, neo4j_driver),
        neo4j_data_probe=partial(check_neo4j_data, neo4j_driver),
        primary_llm=_to_llm_health_target(primary_model),
        backup_llm=(
            _to_llm_health_target(backup_model)
            if backup_model is not None
            else None
        ),
    )


def check_postgresql_connectivity(engine: Engine) -> None:
    """验证PostgreSQL能够建立连接并执行查询。"""

    with engine.connect() as connection:
        connection.execute(text("SELECT 1")).scalar_one()


def check_postgresql_data(engine: Engine) -> None:
    """验证推荐链路依赖的六类PostgreSQL数据均非空。"""

    required_models = (
        Recipe,
        Ingredient,
        RecipeIngredient,
        RecipeNutrition,
        UserProfile,
        ProfileDriTarget,
    )
    with Session(engine) as session:
        missing_tables = [
            model.__tablename__
            for model in required_models
            if session.execute(select(model).limit(1)).first() is None
        ]
    if missing_tables:
        raise RuntimeError("必需业务数据为空")


def check_neo4j_connectivity(neo4j_driver: object) -> None:
    """验证Neo4j驱动能够连接到数据库。"""

    verify_connectivity = getattr(neo4j_driver, "verify_connectivity", None)
    if not callable(verify_connectivity):
        raise RuntimeError("Neo4j驱动不支持连接检查")
    verify_connectivity()


def check_neo4j_data(neo4j_driver: object) -> None:
    """验证图筛选链路依赖的节点与关系均存在。"""

    session_factory = getattr(neo4j_driver, "session", None)
    if not callable(session_factory):
        raise RuntimeError("Neo4j驱动不支持会话")
    with session_factory() as session:
        record = session.run(
            """
            RETURN
              EXISTS { MATCH (:Recipe) } AS has_recipe,
              EXISTS { MATCH (:Ingredient) } AS has_ingredient,
              EXISTS { MATCH (:Concept) } AS has_concept,
              EXISTS { MATCH ()-[:part_of]->() } AS has_part_of,
              EXISTS { MATCH ()-[:is_a]->() } AS has_is_a
            """
        ).single()
    required_fields = (
        "has_recipe",
        "has_ingredient",
        "has_concept",
        "has_part_of",
        "has_is_a",
    )
    if record is None or not all(record[field] for field in required_fields):
        raise RuntimeError("必需图数据为空")


def check_chat_model(chat_model: object) -> None:
    """执行一次最小真实生成并验证模型返回OK。"""

    invoke = getattr(chat_model, "invoke", None)
    if not callable(invoke):
        raise RuntimeError("ChatModel不支持调用")
    response = invoke("健康检查，请仅返回 OK")
    content = getattr(response, "content", response)
    if not isinstance(content, str):
        raise RuntimeError("LLM健康检查响应格式无效")
    normalized = content.strip().upper().strip(".。！!")
    if normalized != "OK":
        raise RuntimeError("LLM健康检查响应内容无效")


def _to_llm_health_target(model: ConfiguredChatModel) -> LlmHealthTarget:
    return LlmHealthTarget(
        model_name=model.model_name,
        probe=partial(check_chat_model, model.chat_model),
    )


__all__ = [
    "check_chat_model",
    "check_neo4j_connectivity",
    "check_neo4j_data",
    "check_postgresql_connectivity",
    "check_postgresql_data",
    "create_health_check_service",
]
