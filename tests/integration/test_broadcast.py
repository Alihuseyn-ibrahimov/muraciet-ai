"""n8n-in bildirişləri (POST /internal/notify): rəhbərlərə, koordinatorlara və ya adminlərə, yalnız aktivlərə."""
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.tables import Outbox
from app.notifier.recipients import broadcast
from app.workflow.states import BotKind
from tests.integration.helpers import COORDINATOR_TG, DEPUTY_TG, HEAD_TG, MONDAY_10

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("target, chats", [
    ("head", {HEAD_TG}), ("coordinators", {COORDINATOR_TG, DEPUTY_TG}), ("admins", {COORDINATOR_TG}),
])
def test_broadcast_reaches_only_active_members_of_the_target(app_engine, world, target, chats):
    with Session(app_engine) as s, s.begin():
        assert broadcast(s, target, "📊 Gündəlik hesabat", now=MONDAY_10) == len(chats)
    with Session(app_engine) as s:
        rows = list(s.scalars(select(Outbox)))
        assert {r.chat_id for r in rows} == chats and {r.bot for r in rows} == {BotKind.staff}
        assert all(r.payload["text"] == "📊 Gündəlik hesabat" for r in rows)


def test_unknown_target_is_rejected(app_engine, world):
    with Session(app_engine) as s, pytest.raises(ValueError):
        broadcast(s, "everyone", "x", now=MONDAY_10)


def test_report_for_heads_goes_to_admins_when_there_is_no_active_head(app_engine, world):
    from app.db.tables import Staff
    with Session(app_engine) as s, s.begin():
        s.get(Staff, world.head.id).is_active = False
        assert broadcast(s, "head", "📊 Hesabat", now=MONDAY_10) == 1
    with Session(app_engine) as s:
        assert [r.chat_id for r in s.scalars(select(Outbox))] == [COORDINATOR_TG]


def test_workday_only_report_is_not_sent_on_a_holiday(app_engine, world):
    from datetime import datetime

    from tests.integration.helpers import BAKU, CAL
    holiday = datetime(2026, 11, 9, 18, tzinfo=BAKU)  # Dövlət Bayrağı Günü
    with Session(app_engine) as s, s.begin():
        assert broadcast(s, "head", "📊", now=holiday, calendar=CAL, only_workday=True) == 0
        assert broadcast(s, "head", "📊", now=MONDAY_10, calendar=CAL, only_workday=True) == 1
    with Session(app_engine) as s:
        assert len(list(s.scalars(select(Outbox)))) == 1
