"""Servis + OutboxNotifier + outbox worker + saxta Telegram: kimə nə gedir, kartlar necə yenilənir."""
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.tables import Outbox, TelegramMessage
from app.notifier.cards import CLOSED
from app.notifier.notifier import OutboxNotifier
from app.notifier.outbox import OutboxWorker
from app.workflow.service import AppealService, RoutingOutcome, TriageOutcome
from app.workflow.states import BotKind, Category, Outcome, OutboxStatus, Priority
from tests.fakes import FakeTelegramClient
from tests.integration.helpers import (
    CAL, CFG, CITIZEN_TG, COORDINATOR_TG, CRYPTO, DEPUTY_TG, HEAD_TG, MONDAY_10, OFF_COORD_TG, ORG, STAFF_A_TG,
    Clock,
)

pytestmark = pytest.mark.integration
PROCEDURE = CFG.templates.texts["appeal_procedure"]


class Telegram:
    """İki bot üçün saxta Telegram və outbox worker-i."""

    def __init__(self, app_engine):
        self.citizen = FakeTelegramClient(first_message_id=5000)
        self.staff = FakeTelegramClient(first_message_id=9000)
        self.worker = OutboxWorker(sessionmaker(app_engine, expire_on_commit=False),
                                   {BotKind.citizen: self.citizen, BotKind.staff: self.staff},
                                   config=CFG.sla.outbox, clock=Clock(MONDAY_10))

    def deliver(self) -> None:
        while self.worker.process_batch():
            pass


@pytest.fixture
def telegram(app_engine) -> Telegram:
    return Telegram(app_engine)


def run(app_engine, fn):
    clock = Clock(MONDAY_10)
    with Session(app_engine, expire_on_commit=False) as s, s.begin():
        notifier = OutboxNotifier(s, config=CFG, crypto=CRYPTO, org_name=ORG, clock=clock)
        return fn(AppealService(s, config=CFG, calendar=CAL, crypto=CRYPTO, notifier=notifier, clock=clock))


def pending(app_engine) -> list[Outbox]:
    with Session(app_engine) as s:
        return list(s.scalars(select(Outbox).where(Outbox.status == OutboxStatus.pending).order_by(Outbox.id)))


def callbacks(payload: dict) -> set[str]:
    return {b["callback_data"] for row in payload.get("reply_markup", {}).get("inline_keyboard", []) for b in row}


def new_appeal(app_engine, world, *, category=Category.request):
    appeal = run(app_engine, lambda svc: svc.create_appeal(
        world.citizen_id, subject="Küçə işıqlandırması", summary="İşıqlar yanmır", description="3 gündür yanmır"))
    return run(app_engine, lambda svc: svc.apply_triage(
        appeal.id, TriageOutcome(category, Priority.normal, False), RoutingOutcome("pensiya", "pensiya ödənişi")))


def assigned(app_engine, world, telegram, **kw):
    appeal = new_appeal(app_engine, world, **kw)
    telegram.deliver()
    appeal = run(app_engine, lambda svc: svc.assign(world.coordinator, appeal.id, world.staff_a.id,
                                                    expected_version=appeal.version))
    telegram.deliver()
    return appeal


def test_new_appeal_card_goes_only_to_active_coordinators(app_engine, world):
    appeal = new_appeal(app_engine, world)
    rows = pending(app_engine)
    assert {r.chat_id for r in rows} == {COORDINATOR_TG, DEPUTY_TG}  # deaktiv koordinator və rəhbər yox
    assert {r.purpose for r in rows} == {"coordinator_card"}
    card = rows[0].payload
    assert appeal.number in card["text"] and "5AB***D" in card["text"]
    assert f"as:{appeal.id}:{appeal.version}:{world.staff_a.id}" in callbacks(card)


def test_assignment_sends_staff_card_and_closes_coordinator_cards(app_engine, world, telegram):
    appeal = new_appeal(app_engine, world)
    telegram.deliver()  # koordinator kartları göndərildi
    appeal = run(app_engine, lambda svc: svc.assign(world.coordinator, appeal.id, world.staff_a.id,
                                                    expected_version=appeal.version))
    rows = pending(app_engine)
    staff_card = next(r for r in rows if r.purpose == "staff_card")
    assert staff_card.chat_id == STAFF_A_TG and "+994501234567" in staff_card.payload["text"]
    edits = [r for r in rows if r.method == "edit_message_text"]
    assert {e.chat_id for e in edits} == {COORDINATOR_TG, DEPUTY_TG}
    assert all(e.payload["reply_markup"] == CLOSED and "Rəşad Hüseynov" in e.payload["text"] for e in edits)
    with Session(app_engine) as s:
        assert all(m.closed_at is not None for m in s.scalars(
            select(TelegramMessage).where(TelegramMessage.purpose == "coordinator_card")))


def test_active_staff_card_is_refreshed_with_new_version(app_engine, world, telegram):
    appeal = assigned(app_engine, world, telegram)
    appeal = run(app_engine, lambda svc: svc.ask_citizen(world.staff_a, appeal.id, "Bina nömrəsi?",
                                                         expected_version=appeal.version))
    edit = next(r for r in pending(app_engine) if r.method == "edit_message_text")
    assert edit.chat_id == STAFF_A_TG and f"an:{appeal.id}:{appeal.version}" in callbacks(edit.payload)


def test_closed_cards_are_never_edited_again(app_engine, world, telegram):
    appeal = assigned(app_engine, world, telegram)  # koordinator kartları burada bağlandı
    run(app_engine, lambda svc: svc.ask_citizen(world.staff_a, appeal.id, "Sual", expected_version=appeal.version))
    edits = [r for r in pending(app_engine) if r.method == "edit_message_text"]
    assert {e.chat_id for e in edits} == {STAFF_A_TG}


def test_question_goes_to_citizen_and_reply_to_assignee(app_engine, world, telegram):
    appeal = assigned(app_engine, world, telegram)
    appeal = run(app_engine, lambda svc: svc.ask_citizen(world.staff_a, appeal.id, "Hansı bina?",
                                                         expected_version=appeal.version))
    question = next(r for r in pending(app_engine) if r.purpose == "citizen_question")
    assert question.chat_id == CITIZEN_TG and "Hansı bina?" in question.payload["text"]
    telegram.deliver()
    run(app_engine, lambda svc: svc.citizen_reply(world.citizen_id, appeal.id, "5 nömrəli bina"))
    info = next(r for r in pending(app_engine) if r.purpose == "staff_info")
    assert info.chat_id == STAFF_A_TG and "5 nömrəli bina" in info.payload["text"]


def test_reply_without_assignee_goes_to_coordinators(app_engine, world):
    appeal = new_appeal(app_engine, world)
    run(app_engine, lambda svc: svc.citizen_reply(world.citizen_id, appeal.id, "Əlavə məlumat"))
    assert {r.chat_id for r in pending(app_engine) if r.purpose == "staff_info"} == {COORDINATOR_TG, DEPUTY_TG}


def test_rejected_answer_has_procedure_and_feedback_buttons(app_engine, world, telegram):
    appeal = assigned(app_engine, world, telegram)
    run(app_engine, lambda svc: svc.submit_answer(world.coordinator, appeal.id, outcome=Outcome.rejected,
                                                  text="Tələbiniz təmin edilmədi.", qa_critical=False,
                                                  expected_version=appeal.version))
    answer = next(r for r in pending(app_engine) if r.purpose == "citizen_answer")
    assert answer.chat_id == CITIZEN_TG and PROCEDURE in answer.payload["text"]  # m. 7.8
    assert callbacks(answer.payload) == {f"fp:{appeal.id}", f"fn:{appeal.id}"}


def test_approval_flow_messages(app_engine, world, telegram):
    appeal = assigned(app_engine, world, telegram, category=Category.complaint)
    appeal = run(app_engine, lambda svc: svc.submit_answer(world.staff_a, appeal.id, outcome=Outcome.satisfied,
                                                           text="İşıqlar bərpa olundu.", qa_critical=False,
                                                           expected_version=appeal.version))
    rows = pending(app_engine)
    for chat in (COORDINATOR_TG, DEPUTY_TG):  # əvvəl cavabın mətni, sonra düyməli kart
        assert [r.purpose for r in rows if r.chat_id == chat] == ["answer_text", "approval_card"]
    assert all(r.purpose != "citizen_answer" for r in rows)  # təsdiqə qədər vətəndaşa getmir
    telegram.deliver()

    run(app_engine, lambda svc: svc.approve(world.deputy, appeal.id, expected_version=appeal.version))
    rows = pending(app_engine)
    assert any(r.purpose == "citizen_answer" and "İşıqlar bərpa olundu." in r.payload["text"] for r in rows)
    approval_edits = [r for r in rows if r.method == "edit_message_text" and r.purpose == "approval_card"]
    assert len(approval_edits) == 2 and all(e.payload["reply_markup"] == CLOSED for e in approval_edits)


def test_reopen_notifies_citizen_and_coordinators(app_engine, world, telegram):
    appeal = assigned(app_engine, world, telegram)
    appeal = run(app_engine, lambda svc: svc.submit_answer(world.coordinator, appeal.id, outcome=Outcome.satisfied,
                                                           text="Həll olundu", qa_critical=False,
                                                           expected_version=appeal.version))
    telegram.deliver()
    run(app_engine, lambda svc: svc.citizen_feedback(world.citizen_id, appeal.id, satisfied=False,
                                                     reason="Yenə yanmır"))
    rows = pending(app_engine)
    assert any(r.chat_id == CITIZEN_TG and appeal.number in r.payload["text"] for r in rows)
    cards_ = [r for r in rows if r.purpose == "coordinator_card" and r.method == "send_message"]
    assert {c.chat_id for c in cards_} == {COORDINATOR_TG, DEPUTY_TG}
    assert all(c.payload["text"].startswith("🔁 Vətəndaş qane olmadı: Yenə yanmır") for c in cards_)


def test_redirect_notice_reaches_citizen(app_engine, world):
    appeal = new_appeal(app_engine, world)
    run(app_engine, lambda svc: svc.redirect(world.coordinator, appeal.id, target="Su Kanal İdarəsi",
                                             letter_ref="12/345", expected_version=appeal.version))
    notice = next(r for r in pending(app_engine) if r.chat_id == CITIZEN_TG)
    assert "Su Kanal İdarəsi" in notice.payload["text"] and "12/345" in notice.payload["text"]


def test_head_and_inactive_coordinator_never_receive_anything(app_engine, world, telegram):
    appeal = assigned(app_engine, world, telegram, category=Category.complaint)
    run(app_engine, lambda svc: svc.submit_answer(world.staff_a, appeal.id, outcome=Outcome.rejected, text="Cavab",
                                                  qa_critical=True, expected_version=appeal.version))
    telegram.deliver()
    chats = {chat for _, chat, _ in telegram.staff.sent + telegram.citizen.sent}
    assert HEAD_TG not in chats and OFF_COORD_TG not in chats
