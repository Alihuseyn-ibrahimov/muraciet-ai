from datetime import date, datetime, timezone

import pytest
from alembic.script import ScriptDirectory
from cryptography.fernet import Fernet
from sqlalchemy import delete, inspect, select, text, update
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app import manage
from app.db.tables import Appeal, AppealEvent, Base, ChatLog, Citizen, Department
from app.privacy.crypto import Crypto
from app.workflow.states import ActorType, AppealStatus
from app.yaml_config import load_config
from tests.integration.conftest import TEST_OWNER_URL, migrate_test_db

pytestmark = pytest.mark.integration

EXTERNAL_TABLES = {"checkpoints", "checkpoint_writes", "langchain_pg_collection", "langchain_pg_embedding"}


def make_citizen(session: Session, telegram_user_id: int = 1001) -> Citizen:
    citizen = Citizen(telegram_user_id=telegram_user_id, last_name="Məmmədov", first_name="Əli")
    session.add(citizen)
    session.flush()
    return citizen


def make_appeal(session: Session, citizen: Citizen, **fields) -> Appeal:
    appeal = Appeal(
        citizen_id=citizen.id,
        subject="Küçə işıqlandırması",
        summary="3 gündür işıqlar yanmır",
        description="Nizami küçəsində 3 gündür işıqlar yanmır",
        due_date=date.today(),
        original_due_date=date.today(),
        **fields,
    )
    session.add(appeal)
    session.flush()
    return appeal


def test_schema_has_our_tables_external_tables_and_view(owner_engine):
    inspector = inspect(owner_engine)
    assert set(Base.metadata.tables) | EXTERNAL_TABLES <= set(inspector.get_table_names())
    assert "v_daily_stats" in inspector.get_view_names()


def test_app_role_can_create_and_update_appeals(app_engine):
    with Session(app_engine) as s, s.begin():
        appeal = make_appeal(s, make_citizen(s))
        appeal.status = AppealStatus.assigned
    with Session(app_engine) as s:
        assert s.scalar(select(Appeal.status)) == AppealStatus.assigned


def test_audit_log_is_append_only_for_app_role(app_engine):
    with Session(app_engine) as s, s.begin():
        appeal = make_appeal(s, make_citizen(s))
        s.add(AppealEvent(appeal_id=appeal.id, actor_type=ActorType.system, action="created"))
    for statement in (update(AppealEvent).values(action="dəyişdirildi"), delete(AppealEvent)):
        with Session(app_engine) as s, pytest.raises(ProgrammingError, match="permission denied"):
            s.execute(statement)
    with Session(app_engine) as s:
        assert s.scalar(select(AppealEvent.action)) == "created"


def test_n8n_role_reads_only_daily_stats(n8n_engine):
    with n8n_engine.connect() as conn:
        assert conn.execute(text("SELECT appeals_created FROM v_daily_stats")).scalar() == 0
    with n8n_engine.connect() as conn, pytest.raises(ProgrammingError, match="permission denied"):
        conn.execute(text("SELECT * FROM citizens"))


def test_sensitive_fields_are_encrypted_at_rest(app_engine, owner_engine):
    crypto = Crypto([Fernet.generate_key().decode()], hmac_key="test-hmac")
    with Session(app_engine) as s, s.begin():
        citizen = make_citizen(s)
        citizen.fin_enc = crypto.encrypt("5ABC12D")
        citizen.fin_hash = crypto.blind_index("fin", "5ABC12D")
        citizen.phone_enc = crypto.encrypt("+994501234567")
        citizen.address_enc = crypto.encrypt("Nizami küç. 5")

    with owner_engine.connect() as conn:
        raw_row = " ".join(str(v) for v in conn.execute(text("SELECT * FROM citizens")).one())
    assert "5ABC12D" not in raw_row and "+994501234567" not in raw_row and "Nizami" not in raw_row

    with Session(app_engine) as s:  # kor indekslə axtarış (kiçik hərflə də)
        found = s.scalar(select(Citizen).where(Citizen.fin_hash == crypto.blind_index("fin", "5abc12d")))
        assert crypto.decrypt(found.fin_enc) == "5ABC12D"


def test_daily_stats_counts_today(app_engine, n8n_engine):
    with Session(app_engine) as s, s.begin():
        citizen = make_citizen(s)
        make_appeal(s, citizen)
        make_appeal(
            s, citizen, status=AppealStatus.answered, answered_at=datetime.now(timezone.utc),
            answer_work_hours=10, satisfaction=1,
        )
        s.add(ChatLog(citizen_telegram_id=1001, role="assistant", content="cavab", resolved_by_ai=True))
    with n8n_engine.connect() as conn:
        row = conn.execute(text("SELECT * FROM v_daily_stats")).mappings().one()
    assert (row["appeals_created"], row["appeals_answered"], row["ai_answers"]) == (2, 1, 1)
    assert (row["open_total"], row["unassigned_total"], row["overdue_total"]) == (1, 1, 0)
    assert float(row["median_answer_work_hours"]) == 10.0
    assert float(row["satisfaction_rate_30d"]) == 1.0


def test_migrate_is_idempotent(owner_engine):
    migrate_test_db()  # ikinci dəfə: heç nə dəyişməməli, xəta verməməlidir
    head = ScriptDirectory.from_config(manage.alembic_config(TEST_OWNER_URL)).get_current_head()
    with owner_engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == head


def test_sync_departments_upserts_and_deactivates(app_engine):
    departments = load_config().departments
    with Session(app_engine) as s, s.begin():
        manage.sync_departments(s, departments)
    with Session(app_engine) as s, s.begin():  # "menzil" YAML-dan çıxarıldı
        manage.sync_departments(s, [d for d in departments if d.code != "menzil"])
    with Session(app_engine) as s:
        active = {d.code: d.is_active for d in s.scalars(select(Department))}
    assert len(active) == len(departments)
    assert active["menzil"] is False and active["general"] is True
