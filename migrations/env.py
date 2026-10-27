"""Alembic mühiti. URL ya çağıranın verdiyi `sqlalchemy.url`-dən, ya da MIGRATION_DATABASE_URL-dən götürülür."""
import warnings

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import settings
from app.db.tables import Base

# langchain_pg_embedding-in 'vector' tipini SQLAlchemy tanımır. Cədvəl xaricidir və nəzərə alınmır (include_object)
warnings.filterwarnings("ignore", message="Did not recognize type 'vector'")

config = context.config
target_metadata = Base.metadata

# Bu cədvəlləri Alembic yox, kitabxanalar özü yaradır (manage.py migrate): LangGraph checkpointer və
# langchain_postgres. Onlar nəzərə alınmasa, autogenerate onları "silinməli" sayıb drop_table yazardı.
EXTERNAL_TABLES = frozenset({
    "checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations",
    "langchain_pg_collection", "langchain_pg_embedding",
})


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    table_name = name if type_ == "table" else getattr(getattr(obj, "table", None), "name", None)
    return not (reflected and compare_to is None and table_name in EXTERNAL_TABLES)


def _url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    settings.require("migration_database_url")
    return settings.migration_database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(), target_metadata=target_metadata, literal_binds=True, compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
