from types import SimpleNamespace
from typing import Any

from backend.infrastructure.graph import importer


class FakePostgreSQLSession:
    """按菜谱、食材、关联的顺序返回待导入数据。"""

    def __init__(self, result_batches: list[list[Any]]) -> None:
        self._result_batches = list(result_batches)

    def scalars(self, statement: Any) -> list[Any]:
        del statement
        return self._result_batches.pop(0)

    def __enter__(self) -> "FakePostgreSQLSession":
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass


class FakeSessionFactory:
    def __init__(self, result_batches: list[list[Any]]) -> None:
        self._result_batches = result_batches

    def __call__(self) -> FakePostgreSQLSession:
        return FakePostgreSQLSession(self._result_batches)


class FakeNeo4jSession:
    def __init__(self) -> None:
        self.executed_query_count = 0
        self.executed_queries: list[tuple[str, dict[str, Any]]] = []

    def run(self, query: str, **params: Any) -> None:
        self.executed_query_count += 1
        self.executed_queries.append((query, params))

    def __enter__(self) -> "FakeNeo4jSession":
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass


class FakeNeo4jDriver:
    def __init__(self) -> None:
        self.neo4j_session = FakeNeo4jSession()
        self.is_closed = False

    def session(self) -> FakeNeo4jSession:
        return self.neo4j_session

    def close(self) -> None:
        self.is_closed = True


def test_图导入按阶段和固定间隔报告进度(monkeypatch) -> None:
    recipes = [
        SimpleNamespace(
            id=index,
            name=f"菜谱{index}",
            labels=[],
            total_time_lower_bound_minutes=10,
            dish_type="菜肴",
            difficulty="简单",
        )
        for index in range(1, 252)
    ]
    ingredients = [SimpleNamespace(id=1, name="食材1", category="蔬菜")]
    recipe_ingredients = [
        SimpleNamespace(recipe_id=1, ingredient_id=1)
    ]
    session_factory = FakeSessionFactory(
        [recipes, ingredients, recipe_ingredients]
    )
    fake_driver = FakeNeo4jDriver()
    monkeypatch.setattr(
        importer,
        "create_neo4j_driver",
        lambda uri, user, password: fake_driver,
    )
    progress_events: list[tuple[str, int, int]] = []

    result = importer.import_graph_data(
        session_factory,
        "bolt://127.0.0.1:7687",
        "neo4j",
        "mealagent",
        progress_callback=lambda stage, completed, total: progress_events.append(
            (stage, completed, total)
        ),
    )

    assert result == {
        "recipes": 251,
        "ingredients": 1,
        "recipe_ingredients": 1,
    }
    assert [
        event for event in progress_events if event[0] == "读取 PostgreSQL"
    ] == [
        ("读取 PostgreSQL", 0, 3),
        ("读取 PostgreSQL", 1, 3),
        ("读取 PostgreSQL", 2, 3),
        ("读取 PostgreSQL", 3, 3),
    ]
    assert [
        event for event in progress_events if event[0] == "写入菜谱节点"
    ] == [
        ("写入菜谱节点", 0, 251),
        ("写入菜谱节点", 250, 251),
        ("写入菜谱节点", 251, 251),
    ]
    for stage in (
        "写入食材节点",
        "写入菜谱食材关系",
        "写入过敏概念关系",
    ):
        stage_events = [event for event in progress_events if event[0] == stage]
        assert stage_events[0][1] == 0
        assert stage_events[-1][1] == stage_events[-1][2]
    assert fake_driver.is_closed is True
    recipe_query, recipe_params = next(
        (query, params)
        for query, params in fake_driver.neo4j_session.executed_queries
        if "MERGE (r:Recipe" in query
    )
    assert "r.difficulty = $difficulty" in recipe_query
    assert recipe_params["difficulty"] == "简单"


def test_图导入写入蟹类概念及七条成员关系() -> None:
    session = FakeNeo4jSession()

    importer._merge_concepts(session)

    concept_calls = [
        params
        for query, params in session.executed_queries
        if "SET c.kind" in query and params.get("name") == "蟹类"
    ]
    relation_members = [
        params["member"]
        for query, params in session.executed_queries
        if "MERGE (i)-[:is_a]->(c)" in query
        and params.get("concept_name") == "蟹类"
    ]
    assert concept_calls == [{"name": "蟹类", "kind": "allergen"}]
    assert relation_members == [
        "大闸蟹",
        "梭子蟹",
        "螃蟹",
        "蟹肉棒",
        "蟹黄",
        "蟹黄/蟹膏",
        "青蟹",
    ]
    assert "蟹味菇" not in relation_members
