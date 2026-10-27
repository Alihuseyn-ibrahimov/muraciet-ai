"""Yeni sistemin DB bağlantısı.

Engine modul səviyyəsində yaranmır: testlər və komponentlər öz URL-lərini ötürə bilir,
standart URL (APP_DATABASE_URL) yalnız ilk istifadədə oxunur.
"""
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings


def make_engine(url: str, **kwargs) -> Engine:
    return create_engine(url, pool_pre_ping=True, **kwargs)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@lru_cache
def get_engine() -> Engine:
    settings.require("app_database_url")
    return make_engine(settings.app_database_url, pool_size=10, max_overflow=5)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return make_session_factory(get_engine())


@contextmanager
def session_scope(factory: sessionmaker[Session] | None = None) -> Iterator[Session]:
    session = (factory or get_session_factory())()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
