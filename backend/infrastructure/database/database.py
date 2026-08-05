from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker


class DatabaseConfigurationError(ValueError):
    """数据库连接配置不符合基础存储规格。"""


def create_database_engine(database_url: str) -> Engine:
    """使用调用方显式提供的URL创建同步数据库Engine。"""

    if not isinstance(database_url, str):
        raise DatabaseConfigurationError("database_url 必须是字符串")
    if not database_url.strip():
        raise DatabaseConfigurationError("database_url 不能为空")

    try:
        return create_engine(database_url, pool_pre_ping=True)
    except ArgumentError as exc:
        raise DatabaseConfigurationError("database_url 格式错误") from exc


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """创建绑定到指定同步Engine的Session工厂。"""

    if not isinstance(engine, Engine):
        raise TypeError("engine 必须是SQLAlchemy同步Engine")
    return sessionmaker(bind=engine)


__all__ = [
    "DatabaseConfigurationError",
    "create_database_engine",
    "create_session_factory",
]
