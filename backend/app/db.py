"""数据库连接与初始化。

PostgreSQL 是 Source of Truth：业务数据 + 结构化查询 + 全文检索 + 向量
全部在一个事务里完成，不需要 Outbox / Kafka / ES Indexer / DLQ。
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import PROJECT_ROOT, settings

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
    return _engine


def get_sessionmaker() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, future=True)
    return _SessionLocal


def get_session() -> Iterator[Session]:
    """FastAPI 依赖。"""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_schema(schema_path: Path | None = None) -> None:
    """执行 db/schema.sql（幂等）。"""
    path = schema_path or (PROJECT_ROOT / "db" / "schema.sql")
    sql = path.read_text(encoding="utf-8")
    with get_engine().begin() as conn:
        conn.execute(text(sql))


def vector_literal(vec: list[float]) -> str:
    """pgvector 字面量：'[0.1,0.2,...]'。"""
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"
