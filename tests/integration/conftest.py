"""İnteqrasiya testləri üçün test bazası (muraciet_test).

- Baza hər pytest sessiyasında sıfırdan yaradılır və `manage.migrate` ilə qurulur, yəni migrasiya
  özü də hər dəfə yoxlanılır.
- Test rolları inkişaf rollarından ayrıdır: rollar bütün klaster üçün ortaqdır, ona görə testlər
  inkişaf parollarının üstünə yazmamalıdır.
- Hər testdən sonra cədvəllər təmizlənir.
- Postgres əlçatan deyilsə, testlər xəta vermir, aydın səbəblə keçilir (skip).
"""
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import manage
from app.db.tables import Base
from tests.integration.helpers import World, build_world

TEST_OWNER_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+psycopg://muraciet:muraciet@127.0.0.1:55432/muraciet_test"
)
APP_ROLE, APP_PASSWORD = "muraciet_test_app", "test_app_password_0001"
N8N_ROLE, N8N_PASSWORD = "muraciet_test_n8n", "test_n8n_password_0001"


def migrate_test_db() -> None:
    manage.migrate(
        TEST_OWNER_URL, app_role=APP_ROLE, app_password=APP_PASSWORD, n8n_role=N8N_ROLE, n8n_password=N8N_PASSWORD
    )


@pytest.fixture(scope="session")
def pg():
    url = make_url(TEST_OWNER_URL)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {url.database} WITH (FORCE)"))
    except OperationalError:
        pytest.skip(f"Test PostgreSQL əlçatan deyil ({url.host}:{url.port}): docker compose up -d postgres")
    finally:
        admin.dispose()

    migrate_test_db()
    engines = {
        "owner": create_engine(url),
        "app": create_engine(url.set(username=APP_ROLE, password=APP_PASSWORD)),
        "n8n": create_engine(url.set(username=N8N_ROLE, password=N8N_PASSWORD)),
    }
    yield engines
    for engine in engines.values():
        engine.dispose()


@pytest.fixture
def owner_engine(pg):
    return pg["owner"]


@pytest.fixture
def app_engine(pg):
    return pg["app"]


@pytest.fixture
def n8n_engine(pg):
    return pg["n8n"]


@pytest.fixture(autouse=True)
def _clean_tables(pg):
    yield
    with pg["owner"].begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(Base.metadata.tables)} RESTART IDENTITY CASCADE"))


TEST_REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://127.0.0.1:56379/15")


@pytest.fixture
def redis_client():
    import redis

    client = redis.Redis.from_url(TEST_REDIS_URL)
    try:
        client.ping()
    except redis.ConnectionError:
        pytest.skip(f"Test Redis əlçatan deyil ({TEST_REDIS_URL}): docker compose up -d redis")
    client.flushdb()
    yield client
    client.flushdb()
    client.close()


@pytest.fixture
def world(pg) -> World:
    """Qurum: 2 aktiv koordinator (biri admin), 1 deaktiv koordinator, Pensiya şöbəsində 2 əməkdaş, rəhbər, 2 vətəndaş."""
    with Session(pg["app"], expire_on_commit=False) as s, s.begin():
        return build_world(s)
