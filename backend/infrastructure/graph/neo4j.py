from __future__ import annotations

import neo4j


class GraphConfigurationError(ValueError):
    """Neo4j 连接配置不符合基础存储规格。"""


def create_neo4j_driver(
    uri: str,
    user: str,
    password: str,
) -> neo4j.Driver:
    """使用调用方显式提供的连接信息创建 Neo4j Driver。"""

    for name, value in (("uri", uri), ("user", user), ("password", password)):
        if not isinstance(value, str):
            raise GraphConfigurationError(f"{name} 必须是字符串")
        if not value.strip():
            raise GraphConfigurationError(f"{name} 不能为空")

    return neo4j.GraphDatabase.driver(
        uri.strip(),
        auth=(user.strip(), password.strip()),
    )


__all__ = [
    "GraphConfigurationError",
    "create_neo4j_driver",
]
