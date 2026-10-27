"""SLA monitoru (spec 11-ci bölmə): konkret `now` dəyərləri ilə cədvəlin hər sətri, 24/7 təcili, təkrarsızlıq."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db.tables import Appeal, Outbox, TelegramMessage
from app.notifier.notifier import CITIZEN_QUESTION, OutboxNotifier
from app.notifier.outbox import OutboxWorker
from app.workflow.service import AppealService, RoutingOutcome, TriageOutcome
from app.workflow.sla import SlaMonitor
from app.workflow.states import AppealStatus, BotKind, Category, Outcome, Priority
from tests.fakes import FakeTelegramClient
from tests.integration.helpers import (
    BAKU, CAL, CFG, CITIZEN_TG, COORDINATOR_TG, CRYPTO, DEPUTY_TG, HEAD_TG, MONDAY_10, OFF_COORD_TG, ORG, STAFF_A_TG,
    Clock,
)

pytestmark = pytest.mark.integration


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """Oktyabr 2026 (19 = bazar ertəsi), Bakı vaxtı."""
    return datetime(2026, 10, day, hour, minute, tzinfo=BAKU)


class World:
    def __init__(self, app_engine, world):
        self.w = world
        self.factory = sessionmaker(app_engine, expire_on_commit=False)
        self.staff_tg, self.citizen_tg = FakeTelegramClient(), FakeTelegramClient()
        self.clock = Clock(MONDAY_10)  # outbox worker-in saatı yoxlama vaxtı ilə birlikdə irəliləyir
        self.worker = OutboxWorker(self.factory, {BotKind.staff: self.staff_tg, BotKind.citizen: self.citizen_tg},
                                   config=CFG.sla.outbox, clock=self.clock)
        self.monitor = SlaMonitor(self.factory, config=CFG, calendar=CAL, crypto=CRYPTO, org_name=ORG)

    def service(self, fn, when: datetime = MONDAY_10):
        with self.factory() as s, s.begin():
            clock = Clock(when)
            notifier = OutboxNotifier(s, config=CFG, crypto=CRYPTO, org_name=ORG, clock=clock)
            result = fn(AppealService(s, config=CFG, calendar=CAL, crypto=CRYPTO, notifier=notifier, clock=clock))
        self.clock.moment = when
        self.deliver()
        return result

    def deliver(self):
        while self.worker.process_batch():
            pass

    def appeal(self, when: datetime = MONDAY_10, priority: Priority = Priority.normal) -> Appeal:
        def create(svc):
            appeal = svc.create_appeal(self.w.citizen_id, subject="Pensiya", summary="Köçmür", description="Köçmür")
            return svc.apply_triage(appeal.id, TriageOutcome(Category.request, priority, False),
                                    RoutingOutcome("pensiya", "pensiya"))
        return self.service(create, when)

    def assigned(self, when: datetime = MONDAY_10) -> Appeal:
        appeal = self.appeal(when)
        return self.service(lambda svc: svc.assign(self.w.coordinator, appeal.id, self.w.staff_a.id,
                                                   expected_version=appeal.version), when)

    def check(self, now: datetime) -> dict[int, list[str]]:
        """SLA yoxlaması → hər chat-a gedən yeni mesajlar."""
        seen = {"staff": len(self.staff_tg.sent), "citizen": len(self.citizen_tg.sent)}
        self.monitor.check(now)
        self.clock.moment = now
        self.deliver()
        out: dict[int, list[str]] = {}
        for client, key in ((self.staff_tg, "staff"), (self.citizen_tg, "citizen")):
            for method, chat, payload in client.sent[seen[key]:]:
                if method == "send_message":
                    out.setdefault(chat, []).append(payload["text"])
        return out


@pytest.fixture
def sla(app_engine, world) -> World:
    return World(app_engine, world)


# ---------- təyin olunmayan müraciət ----------
def test_unassigned_appeal_escalates_to_coordinators_then_to_the_head(sla):
    appeal = sla.appeal(at(19, 10))
    assert sla.check(at(19, 13, 59)) == {}
    coordinators = sla.check(at(19, 14))  # 4 iş saatı
    assert set(coordinators) == {COORDINATOR_TG, DEPUTY_TG} and appeal.number in coordinators[COORDINATOR_TG][0]
    assert OFF_COORD_TG not in coordinators  # deaktiv koordinator
    assert sla.check(at(19, 15)) == {}  # təkrar yoxdur
    assert sla.check(at(19, 18)) == {}  # 8 iş saatı 18:00-da dolur, amma iş vaxtı bitib
    assert list(sla.check(at(20, 9))) == [HEAD_TG]  # səhər


def test_work_hours_skip_the_night_and_notices_wait_for_working_time(sla):
    sla.appeal(at(19, 16))  # bazar ertəsi 16:00 → 4 iş saatı çərşənbə axşamı 11:00-da dolur
    assert sla.check(at(19, 22)) == {}
    assert sla.check(at(20, 10, 59)) == {}
    assert set(sla.check(at(20, 11))) == {COORDINATOR_TG, DEPUTY_TG}


def test_non_urgent_notice_is_not_sent_outside_working_hours(sla):
    sla.appeal(at(23, 14))  # cümə 14:00 → 4 iş saatı cümə 18:00-da dolur
    assert sla.check(at(23, 18, 30)) == {}  # iş vaxtı bitib
    assert sla.check(at(24, 12)) == {}  # şənbə
    assert set(sla.check(at(26, 9, 5))) == {COORDINATOR_TG, DEPUTY_TG}  # bazar ertəsi


def test_urgent_appeal_uses_calendar_hours_around_the_clock(sla):
    sla.appeal(at(19, 22), priority=Priority.urgent)  # gecə
    assert sla.check(at(19, 22, 59)) == {}
    assert set(sla.check(at(19, 23))) == {COORDINATOR_TG, DEPUTY_TG}  # 1 saat, gecə də
    assert list(sla.check(at(20, 0))) == [HEAD_TG]  # 2 saat


# ---------- son tarix ----------
def test_due_date_reminders(sla):
    appeal = sla.assigned(at(19, 10))
    due = appeal.due_date
    day = due
    for _ in range(3):  # son tarixdən 3 iş günü geri
        day -= timedelta(days=1)
        while not CAL.is_workday(day):
            day -= timedelta(days=1)
    first = sla.check(datetime.combine(day, datetime.min.time(), BAKU).replace(hour=10))
    assert list(first) == [STAFF_A_TG] and "3 iş günü" in first[STAFF_A_TG][0]
    one_day = due - timedelta(days=1)
    while not CAL.is_workday(one_day):
        one_day -= timedelta(days=1)
    second = sla.check(datetime.combine(one_day, datetime.min.time(), BAKU).replace(hour=10))
    assert set(second) == {STAFF_A_TG, COORDINATOR_TG, DEPUTY_TG} and "1 iş günü" in second[STAFF_A_TG][0]


def test_overdue_appeals_go_to_the_head_once_a_day_in_one_digest(sla):
    first, second = sla.assigned(at(19, 10)), sla.assigned(at(19, 10))
    after_due = first.due_date + timedelta(days=1)
    while not CAL.is_workday(after_due):
        after_due += timedelta(days=1)
    morning = datetime.combine(after_due, datetime.min.time(), BAKU).replace(hour=10)
    digest = sla.check(morning)
    assert len(digest[HEAD_TG]) == 1 and first.number in digest[HEAD_TG][0] and second.number in digest[HEAD_TG][0]
    assert HEAD_TG not in sla.check(morning + timedelta(hours=3))
    next_day = after_due + timedelta(days=1)
    while not CAL.is_workday(next_day):
        next_day += timedelta(days=1)
    assert HEAD_TG in sla.check(datetime.combine(next_day, datetime.min.time(), BAKU).replace(hour=10))


# ---------- vətəndaşın cavabı gözlənilir ----------
def test_waiting_citizen_gets_a_reminder_and_the_executor_gets_options(sla):
    appeal = sla.assigned(at(19, 10))
    sla.service(lambda svc: svc.ask_citizen(sla.w.staff_a, appeal.id, "Hansı bankdır?",
                                            expected_version=appeal.version), at(19, 11))
    assert sla.check(at(23, 12)) == {}  # 4 iş günü
    reminder = sla.check(at(26, 12))  # 5 iş günü (cümə → bazar ertəsi)
    assert list(reminder) == [CITIZEN_TG] and "Hansı bankdır?" in reminder[CITIZEN_TG][0]
    with sla.factory() as s:  # xatırlatmaya reply də müraciətə bağlanır
        purposes = [m.purpose for m in s.scalars(select(TelegramMessage).where(TelegramMessage.bot == BotKind.citizen))]
        assert purposes.count(CITIZEN_QUESTION) == 2
    assert sla.check(at(27, 12)) == {}
    prompt = sla.check(datetime(2026, 11, 2, 12, tzinfo=BAKU))  # 10 iş günü
    assert list(prompt) == [STAFF_A_TG] and "8.1.5" in prompt[STAFF_A_TG][0]


# ---------- avtomatik bağlanma ----------
def test_answered_appeal_closes_after_seven_days_without_feedback(sla):
    appeal = sla.assigned(at(19, 10))
    sla.service(lambda svc: svc.submit_answer(sla.w.staff_a, appeal.id, outcome=Outcome.satisfied,
                                              text="Pensiya köçürüldü.", qa_critical=False,
                                              expected_version=appeal.version), at(19, 12))
    sla.check(at(26, 11))
    with sla.factory() as s:
        assert s.get(Appeal, appeal.id).status == AppealStatus.answered
    summary = sla.monitor.check(at(26, 12, 1))
    assert summary["auto_closed"] == 1
    with sla.factory() as s:
        assert s.get(Appeal, appeal.id).status == AppealStatus.closed


def test_check_returns_a_summary_and_sends_nothing_without_appeals(sla):
    assert sla.monitor.check(at(19, 12)) == {"notified": 0, "auto_closed": 0}
    with sla.factory() as s:
        assert s.scalar(select(Outbox.id)) is None


def test_missed_checks_send_only_the_most_urgent_due_reminder(sla):
    # Yoxlama günlərlə işləməyibsə (n8n dayanıb), son gün "3 gün qalıb" yazılmamalı və iki mesaj getməməlidir
    appeal = sla.assigned(at(19, 10))
    one_day = appeal.due_date - timedelta(days=1)
    while not CAL.is_workday(one_day):
        one_day -= timedelta(days=1)
    morning = datetime.combine(one_day, datetime.min.time(), BAKU).replace(hour=10)
    first = sla.check(morning)
    assert len(first[STAFF_A_TG]) == 1 and "1 iş günü" in first[STAFF_A_TG][0]
    assert STAFF_A_TG not in sla.check(morning + timedelta(hours=1))
