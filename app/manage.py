"""İdarəetmə əmrləri:  python -m app.manage <əmr>

  create-db    bazanı yaradır (yoxdursa)
  migrate      create-db → Alembic → LangGraph checkpoint cədvəlləri → vektor cədvəlləri → rollar və icazələr
  sync-config  config/departments.yaml → departments cədvəli
  ingest       data/knowledge → bilik bazası (embedding: EMBEDDINGS_URL)
  invite       əməkdaş botu üçün birdəfəlik dəvət linki, məs. ilk admin:
               python -m app.manage invite coordinator --admin
               python -m app.manage invite staff --department pensiya

Bütün addımlar idempotentdir: əmri təkrar işə salmaq təhlükəsizdir.
"""
import argparse
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import settings
from app.db import roles
from app.db.tables import Department
from app.yaml_config import Department as DepartmentConfig
from app.yaml_config import get_config

ROOT = Path(__file__).resolve().parents[1]
VECTOR_COLLECTION = "knowledge_base"
log = logging.getLogger("manage")


def create_database(owner_url: str) -> bool:
    """Bazanı yaradır. Yaradıldısa True, artıq var idisə False qaytarır."""
    url = make_url(owner_url)
    name = roles._ident(url.database)
    engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            if conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}).scalar():
                return False
            conn.execute(text(f"CREATE DATABASE {name}"))
            return True
    finally:
        engine.dispose()


def alembic_config(owner_url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", owner_url.replace("%", "%%"))
    return cfg


def setup_checkpointer(owner_url: str) -> None:
    from langgraph.checkpoint.postgres import PostgresSaver

    conninfo = owner_url.replace("postgresql+psycopg://", "postgresql://")
    with PostgresSaver.from_conn_string(conninfo) as saver:
        saver.setup()


def setup_vector_tables(owner_url: str) -> None:
    # Cədvəlləri sahib rolu yaradır; tətbiq rolu sonra yalnız oxuyur/yazır.
    # Embedding modeli burada lazım deyil, ona görə saxta embedding ilə yalnız sxem qurulur.
    from langchain_core.embeddings import DeterministicFakeEmbedding
    from langchain_postgres import PGVector

    PGVector(
        embeddings=DeterministicFakeEmbedding(size=8),
        collection_name=VECTOR_COLLECTION,
        connection=owner_url,
        use_jsonb=True,
        create_extension=False,  # extension migrasiyada yaradılır
    )


def migrate(
    owner_url: str,
    *,
    app_role: str,
    app_password: str,
    n8n_role: str,
    n8n_password: str,
) -> None:
    if create_database(owner_url):
        log.info("baza yaradıldı: %s", make_url(owner_url).database)
    command.upgrade(alembic_config(owner_url), "head")
    setup_checkpointer(owner_url)
    setup_vector_tables(owner_url)
    engine = create_engine(owner_url)
    try:
        with engine.begin() as conn:
            roles.ensure_role(conn, app_role, app_password, "APP_DB_PASSWORD")
            roles.ensure_role(conn, n8n_role, n8n_password, "N8N_DB_PASSWORD")
            roles.apply_grants(conn, make_url(owner_url).database, app_role, n8n_role)
    finally:
        engine.dispose()


def sync_departments(session: Session, departments: list[DepartmentConfig]) -> None:
    """YAML-dakı şöbələri bazaya yazır. YAML-da olmayan şöbə silinmir, deaktiv edilir
    (köhnə müraciətlər ona istinad edə bilər)."""
    existing = {d.code: d for d in session.scalars(select(Department))}
    for cfg in departments:
        row = existing.pop(cfg.code, None) or Department(code=cfg.code)
        row.name, row.competence, row.is_active = cfg.name, cfg.competence, cfg.active
        session.add(row)
    for leftover in existing.values():
        leftover.is_active = False


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="python -m app.manage")
    parser.add_argument("command", choices=["create-db", "migrate", "sync-config", "ingest", "invite", "preflight"])
    parser.add_argument("role", nargs="?", choices=["coordinator", "staff", "head"], help="invite üçün rol")
    parser.add_argument("--admin", action="store_true", help="invite: admin hüququ (/devet, /emekdaslar)")
    parser.add_argument("--department", help="invite: şöbənin kodu (icraçı üçün)")
    args = parser.parse_args()

    if args.command == "preflight":
        preflight()
        return
    settings.require("migration_database_url")
    owner_url = settings.migration_database_url
    if args.command == "create-db":
        print("yaradıldı" if create_database(owner_url) else "artıq var")
    elif args.command == "migrate":
        settings.require("app_db_password", "n8n_db_password")
        migrate(
            owner_url,
            app_role=settings.app_db_role,
            app_password=settings.app_db_password,
            n8n_role=settings.n8n_db_role,
            n8n_password=settings.n8n_db_password,
        )
        print("migrasiya tamamlandı")
    elif args.command == "sync-config":
        engine = create_engine(owner_url)
        with Session(engine) as session, session.begin():
            sync_departments(session, get_config().departments)
        print("şöbələr sinxronlaşdırıldı")
    elif args.command == "ingest":
        from app.knowledge.kb import ingest

        print(f"{ingest(owner_url)} parça bilik bazasına yükləndi")
    elif args.command == "invite":
        print(invite(owner_url, role=args.role, is_admin=args.admin, department_code=args.department))


def invite(owner_url: str, *, role: str | None, is_admin: bool, department_code: str | None) -> str:
    from datetime import UTC, datetime

    from app.staff.invites import INVITE_HOURS, create_invite, invite_link
    from app.workflow.states import StaffRole

    if role is None:
        raise SystemExit("Rolu göstərin: coordinator, staff və ya head")
    settings.require("staff_bot_username")
    with Session(create_engine(owner_url)) as session, session.begin():
        department_id = None
        if department_code:
            department = session.scalar(select(Department).where(Department.code == department_code))
            if department is None:
                raise SystemExit(f"Şöbə tapılmadı: {department_code}")
            department_id = department.id
        token = create_invite(session, role=StaffRole(role), department_id=department_id, is_admin=is_admin,
                              created_by_staff_id=None, now=datetime.now(UTC))
    return (f"{invite_link(settings.staff_bot_username, token)}\n"
            f"Link {INVITE_HOURS} saat və bir dəfə etibarlıdır. Yalnız həmin şəxsə göndərin.")


def preflight() -> None:
    """Production-dan əvvəl: sirlər və parametrlər (yalnız adlar çap olunur). Xəta varsa çıxış kodu 1."""
    import sys

    from app.preflight import check

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    problems = check(settings)
    for problem in problems:
        print(f"{'XƏTA' if problem.level == 'error' else 'xəbərdarlıq'}: {problem.message}")
    errors = sum(problem.level == "error" for problem in problems)
    print(f"yoxlama: {errors} xəta, {len(problems) - errors} xəbərdarlıq")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
