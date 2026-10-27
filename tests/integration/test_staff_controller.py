"""Əməkdaş botunun kontrolleri: qeydiyyat, kartların düymələri, cavab axını, təsdiq, admin əmrləri (spec 7-ci bölmə)."""
import itertools
import logging
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.agents.schemas import QaIssue, QaReport
from app.citizen.session import MemoryLimiter, MemorySessionStore
from app.db.tables import Appeal, AppealEvent, Job, Outbox, Staff
from app.notifier.cards import (
    ANSWER, APPROVE, ASK, ASSIGN, FIN, NOT_REVIEW, OTHER, REDIRECT, RETURN, SEND_BACK, USE_DRAFT, callback,
)
from app.notifier.notifier import OutboxNotifier
from app.notifier.outbox import OutboxWorker
from app.staff.controller import (
    DEACTIVATE, EXTEND_DAYS, INVITE_DEPT, INVITE_ROLE, NOT_REVIEW_REASON, OUTCOME, SEND, SKIP_LETTER, StaffController,
)
from app.staff.session import StaffSession
from app.workflow.service import AppealService, RoutingOutcome, TriageOutcome
from app.workflow.states import AppealStatus, BotKind, Category, MessageKind, Outcome, Priority, StaffRole
from tests.fakes import FakeTelegramClient
from tests.integration.helpers import (
    CAL, CFG, COORDINATOR_TG, CRYPTO, DEPUTY_TG, HEAD_TG, MONDAY_10, OFF_COORD_TG, ORG, STAFF_A_TG, STAFF_B_TG, Clock,
)

pytestmark = pytest.mark.integration
STRANGER_TG = 7777


class ScriptedQa:
    def __init__(self, report: QaReport | None = None):
        self.report, self.calls = report or QaReport(), []

    def __call__(self, **kwargs) -> QaReport:
        self.calls.append(kwargs)
        return self.report


class StaffBot:
    def __init__(self, app_engine, qa: ScriptedQa | None = None):
        self.clock = Clock(MONDAY_10)
        self.factory = sessionmaker(app_engine, expire_on_commit=False)
        self.staff_tg = FakeTelegramClient(first_message_id=9000)
        self.citizen_tg = FakeTelegramClient(first_message_id=5000)
        self.worker = OutboxWorker(self.factory, {BotKind.staff: self.staff_tg, BotKind.citizen: self.citizen_tg},
                                   config=CFG.sla.outbox, clock=self.clock, crypto=CRYPTO)
        self.qa = qa or ScriptedQa()
        self.controller = StaffController(
            self.factory, store=MemorySessionStore(StaffSession), limiter=MemoryLimiter(), client=self.staff_tg,
            config=CFG, calendar=CAL, crypto=CRYPTO, org_name=ORG, qa=self.qa, bot_username="test_staff_bot",
            clock=self.clock)
        self._ids = itertools.count(1)

    def deliver(self) -> None:
        while self.worker.process_batch():
            pass

    def _handle(self, update: dict, user: int) -> list[dict]:
        seen = len(self.staff_tg.sent)
        self.controller.handle(update)
        self.deliver()
        return [p for m, chat, p in self.staff_tg.sent[seen:] if m == "send_message" and chat == user]

    def say(self, text: str, *, user: int, first_name: str = "Yeni", last_name: str = "Əməkdaş") -> list[dict]:
        n = next(self._ids)
        message = {"message_id": n, "from": {"id": user, "first_name": first_name, "last_name": last_name},
                   "chat": {"id": user, "type": "private"}, "date": 0, "text": text}
        return self._handle({"update_id": n, "message": message}, user)

    def press(self, data: str, *, user: int) -> tuple[list[dict], dict]:
        n = next(self._ids)
        query = {"id": f"cb{n}", "from": {"id": user, "first_name": "X"}, "data": data,
                 "message": {"message_id": 1, "chat": {"id": user, "type": "private"}}}
        messages = self._handle({"update_id": n, "callback_query": query}, user)
        answers = [p for m, _, p in self.staff_tg.sent if m == "answer_callback_query" and p["id"] == f"cb{n}"]
        return messages, answers[0]

    def service(self, fn):
        with self.factory() as s, s.begin():
            notifier = OutboxNotifier(s, config=CFG, crypto=CRYPTO, org_name=ORG, clock=self.clock)
            result = fn(AppealService(s, config=CFG, calendar=CAL, crypto=CRYPTO, notifier=notifier, clock=self.clock))
        self.deliver()
        return result

    def appeal(self, appeal_id: int) -> Appeal:
        with self.factory() as s:
            return s.get(Appeal, appeal_id)


def texts(messages: list[dict]) -> str:
    return "\n".join(m["text"] for m in messages)


def buttons(messages: list[dict]) -> list[str]:
    return [b["callback_data"] for m in messages for row in (m.get("reply_markup") or {}).get("inline_keyboard", [])
            for b in row]


def triaged(bot: StaffBot, world, *, category=Category.request, priority=Priority.normal) -> Appeal:
    def create(svc):
        appeal = svc.create_appeal(world.citizen_id, subject="Pensiya köçmür", summary="Pensiya 3 aydır köçmür",
                                   description="Pensiyam 3 aydır köçmür, bank kartına pul gəlmir")
        return svc.apply_triage(appeal.id, TriageOutcome(category, priority, False),
                                RoutingOutcome("pensiya", "pensiya ödənişi"))
    return bot.service(create)


def assigned(bot: StaffBot, world, **kw) -> Appeal:
    appeal = triaged(bot, world, **kw)
    return bot.service(lambda svc: svc.assign(world.coordinator, appeal.id, world.staff_a.id,
                                              expected_version=appeal.version))


def write_answer(bot: StaffBot, appeal: Appeal, outcome: Outcome, text: str, *, user: int = STAFF_A_TG):
    bot.press(callback(ANSWER, appeal.id, appeal.version), user=user)
    bot.press(callback(OUTCOME, appeal.id, appeal.version, outcome.value), user=user)
    return bot.say(text, user=user)


# ---------- identifikasiya və qeydiyyat ----------
def test_unregistered_and_deactivated_people_are_refused_and_logged(app_engine, world, caplog):
    bot = StaffBot(app_engine)
    with caplog.at_level(logging.WARNING):
        assert "yalnız qurum əməkdaşları" in texts(bot.say("/panel", user=STRANGER_TG))
        assert "yalnız qurum əməkdaşları" in texts(bot.say("/panel", user=OFF_COORD_TG))
    assert str(STRANGER_TG) in caplog.text and str(OFF_COORD_TG) in caplog.text


def invite_link(bot: StaffBot, world) -> str:
    bot.say("/devet", user=COORDINATOR_TG)
    bot.press(f"{INVITE_ROLE}:staff", user=COORDINATOR_TG)
    link = texts(bot.press(f"{INVITE_DEPT}:{world.pensiya_id}", user=COORDINATOR_TG)[0])
    assert "https://t.me/test_staff_bot?start=inv_" in link
    return link.split("start=")[1].split()[0]


def test_invite_link_registers_once(app_engine, world):
    bot = StaffBot(app_engine)
    start = invite_link(bot, world)
    welcome = bot.say(f"/start {start}", user=STRANGER_TG, first_name="Aysel", last_name="Kərimova")
    assert "xoş gəlmisiniz" in texts(welcome).lower()
    with bot.factory() as s:
        staff = s.scalar(select(Staff).where(Staff.telegram_user_id == STRANGER_TG))
        assert (staff.full_name, staff.role, staff.department_id) == ("Aysel Kərimova", StaffRole.staff,
                                                                       world.pensiya_id)
    assert "etibarsız" in texts(bot.say(f"/start {start}", user=8888))  # ikinci istifadə


def test_expired_invite_is_rejected(app_engine, world):
    bot = StaffBot(app_engine)
    start = invite_link(bot, world)
    bot.clock.moment = MONDAY_10 + timedelta(hours=25)
    assert "etibarsız" in texts(bot.say(f"/start {start}", user=STRANGER_TG))


def test_sent_invite_link_does_not_stay_in_the_outbox(app_engine, world):
    from app.db.tables import Outbox
    from app.notifier.outbox import REDACTED, SECRET_PURPOSE

    bot = StaffBot(app_engine)
    start = invite_link(bot, world)  # əməkdaş linki alıb
    with bot.factory() as s:
        outbox = list(s.scalars(select(Outbox)))
    assert start not in str([row.payload for row in outbox])
    assert [row.payload for row in outbox if row.purpose == SECRET_PURPOSE] == [{"text": REDACTED}]


def test_only_admins_create_invites(app_engine, world):
    bot = StaffBot(app_engine)
    assert "admin" in texts(bot.say("/devet", user=DEPUTY_TG))


# ---------- təyinat ----------
def test_assignment_sends_the_staff_card_and_creates_a_draft_job(app_engine, world):
    bot = StaffBot(app_engine)
    appeal = triaged(bot, world)
    card = [p for m, chat, p in bot.staff_tg.sent if chat == COORDINATOR_TG]
    assign = callback(ASSIGN, appeal.id, appeal.version, world.staff_a.id)
    assert assign in buttons(card)
    bot.press(assign, user=COORDINATOR_TG)
    staff_card = [p for m, chat, p in bot.staff_tg.sent if chat == STAFF_A_TG]
    assert appeal.number in texts(staff_card) and "Məmmədov Əli Vəli oğlu" in texts(staff_card)
    with bot.factory() as s:
        assert s.scalar(select(Job.kind).where(Job.appeal_id == appeal.id, Job.kind == "draft")) == "draft"
    assert bot.appeal(appeal.id).status == AppealStatus.assigned


def test_other_staff_list_offers_every_active_executor_including_coordinators(app_engine, world):
    # Kiçik qurumda koordinator müraciəti özü də icra edə bilər; rəhbər və deaktiv əməkdaş siyahıda olmur
    bot = StaffBot(app_engine)
    appeal = triaged(bot, world)
    listing, _ = bot.press(callback(OTHER, appeal.id, appeal.version), user=COORDINATOR_TG)
    assert set(buttons(listing)) == {callback(ASSIGN, appeal.id, appeal.version, person.id)
                                     for person in (world.staff_a, world.staff_b, world.coordinator, world.deputy)}
    bot.press(callback(ASSIGN, appeal.id, appeal.version, world.coordinator.id), user=COORDINATOR_TG)
    assert bot.appeal(appeal.id).assigned_staff_id == world.coordinator.id


def test_stale_button_is_refused(app_engine, world):
    bot = StaffBot(app_engine)
    appeal = triaged(bot, world)
    bot.press(callback(ASSIGN, appeal.id, appeal.version, world.staff_a.id), user=COORDINATOR_TG)
    _, answer = bot.press(callback(ASSIGN, appeal.id, appeal.version, world.staff_b.id), user=DEPUTY_TG)
    assert "köhnəlib" in answer["text"]
    assert bot.appeal(appeal.id).assigned_staff_id == world.staff_a.id


# ---------- cavab axını ----------
def test_answer_with_critical_qa_issue_goes_to_approval(app_engine, world):
    critical = QaReport(issues=[QaIssue(severity="critical", code="pii_other_person", message="Başqasının nömrəsi")])
    bot = StaffBot(app_engine, ScriptedQa(critical))
    appeal = assigned(bot, world)
    preview = write_answer(bot, appeal, Outcome.satisfied, "Pensiyanız köçürüldü. Qonşu: 0552223344")
    assert "Önizləmə" in texts(preview) and "🔴" in texts(preview) and "Təsdiqə göndər" in str(preview)
    assert bot.qa.calls[0]["answer"].startswith("Pensiyanız köçürüldü")
    sent = bot.press(callback(SEND, appeal.id, appeal.version), user=STAFF_A_TG)[0]
    assert "təsdiqinə göndərildi" in texts(sent)
    assert bot.appeal(appeal.id).status == AppealStatus.pending_approval
    approval = [p for m, chat, p in bot.staff_tg.sent if chat == COORDINATOR_TG and "Təsdiq gözləyir" in p["text"]]
    assert approval


def test_approval_sends_the_answer_to_the_citizen(app_engine, world):
    bot = StaffBot(app_engine)
    appeal = assigned(bot, world, category=Category.complaint)  # şikayət → təsdiq
    write_answer(bot, appeal, Outcome.satisfied, "Pensiyanız 24.10.2026 tarixində köçürüldü.")
    bot.press(callback(SEND, appeal.id, appeal.version), user=STAFF_A_TG)
    pending = bot.appeal(appeal.id)
    assert pending.status == AppealStatus.pending_approval
    bot.press(callback(APPROVE, pending.id, pending.version), user=COORDINATOR_TG)
    assert bot.appeal(appeal.id).status == AppealStatus.answered
    citizen = [p["text"] for m, _, p in bot.citizen_tg.sent if m == "send_message"]
    assert any("24.10.2026 tarixində köçürüldü" in t and appeal.number in t for t in citizen)


def test_send_back_returns_the_answer_with_a_comment(app_engine, world):
    bot = StaffBot(app_engine)
    appeal = assigned(bot, world, category=Category.complaint)
    write_answer(bot, appeal, Outcome.satisfied, "Köçürüldü.")
    bot.press(callback(SEND, appeal.id, appeal.version), user=STAFF_A_TG)
    pending = bot.appeal(appeal.id)
    bot.press(callback(SEND_BACK, pending.id, pending.version), user=COORDINATOR_TG)
    bot.say("Tarixi və məbləği göstərin", user=COORDINATOR_TG)
    assert bot.appeal(appeal.id).status == AppealStatus.assigned
    to_staff = [p["text"] for m, chat, p in bot.staff_tg.sent if chat == STAFF_A_TG]
    assert any("Tarixi və məbləği göstərin" in t for t in to_staff)


def test_coordinators_own_answer_skips_approval(app_engine, world):
    bot = StaffBot(app_engine)
    appeal = assigned(bot, world, category=Category.complaint)
    card = bot.say(f"/tap {appeal.number}", user=COORDINATOR_TG)
    assert callback(ANSWER, appeal.id, appeal.version) in buttons(card)
    preview = write_answer(bot, appeal, Outcome.satisfied, "Pensiyanız köçürüldü.", user=COORDINATOR_TG)
    assert "Təsdiqə göndər" not in str(preview)
    bot.press(callback(SEND, appeal.id, appeal.version), user=COORDINATOR_TG)
    assert bot.appeal(appeal.id).status == AppealStatus.answered


def test_use_draft_as_answer(app_engine, world):
    bot = StaffBot(app_engine)
    appeal = assigned(bot, world)
    with bot.factory() as s, s.begin():
        from app.db.tables import AppealMessage
        draft = AppealMessage(appeal_id=appeal.id, kind=MessageKind.ai_draft, text="AI layihəsi: pensiya köçürüləcək")
        s.add(draft)
        s.flush()
        draft_id = draft.id
    outcome_buttons, _ = bot.press(callback(USE_DRAFT, appeal.id, appeal.version, draft_id), user=STAFF_A_TG)
    preview, _ = bot.press(callback(OUTCOME, appeal.id, appeal.version, Outcome.explained.value), user=STAFF_A_TG)
    assert "AI layihəsi: pensiya köçürüləcək" in texts(preview) and "Önizləmə" in texts(preview)


def test_long_text_is_refused(app_engine, world):
    bot = StaffBot(app_engine)
    appeal = assigned(bot, world)
    assert "4000" in texts(write_answer(bot, appeal, Outcome.satisfied, "ə" * 4001))
    assert bot.qa.calls == []


# ---------- digər düymələr ----------
def test_ask_citizen_and_return_to_coordinator(app_engine, world):
    bot = StaffBot(app_engine)
    appeal = assigned(bot, world)
    bot.press(callback(ASK, appeal.id, appeal.version), user=STAFF_A_TG)
    bot.say("Pensiya hansı banka köçürülürdü?", user=STAFF_A_TG)
    asked = bot.appeal(appeal.id)
    assert asked.status == AppealStatus.waiting_citizen
    assert any("hansı banka" in p["text"] for m, _, p in bot.citizen_tg.sent if m == "send_message")
    bot.press(callback(RETURN, asked.id, asked.version), user=STAFF_A_TG)
    bot.say("Bu, DSMF-in işidir", user=STAFF_A_TG)
    assert bot.appeal(appeal.id).status == AppealStatus.new


def test_other_staff_cannot_act_on_my_appeal(app_engine, world):
    bot = StaffBot(app_engine)
    appeal = assigned(bot, world)
    _, answer = bot.press(callback(ANSWER, appeal.id, appeal.version), user=STAFF_B_TG)
    assert "icazə" in answer["text"].lower()


def test_fin_is_shown_privately_and_audited(app_engine, world):
    bot = StaffBot(app_engine)
    appeal = assigned(bot, world)
    messages, answer = bot.press(callback(FIN, appeal.id, appeal.version), user=STAFF_A_TG)
    assert "5ABC12D" in answer["text"] and answer["show_alert"] and messages == []
    with bot.factory() as s:
        assert s.scalar(select(AppealEvent.action).where(AppealEvent.action == "pii_viewed")) == "pii_viewed"
        assert not [o for o in s.scalars(select(Outbox)) if "5ABC12D" in str(o.payload)]


def test_redirect_with_letter_number(app_engine, world):
    bot = StaffBot(app_engine)
    appeal = triaged(bot, world)
    bot.press(callback(REDIRECT, appeal.id, appeal.version), user=COORDINATOR_TG)
    bot.say("Dövlət Sosial Müdafiə Fondu", user=COORDINATOR_TG)
    bot.say("№ 12/345, 21.10.2026", user=COORDINATOR_TG)
    row = bot.appeal(appeal.id)
    assert row.status == AppealStatus.redirected
    notice = [p["text"] for m, _, p in bot.citizen_tg.sent if m == "send_message"][-1]
    assert "Dövlət Sosial Müdafiə Fondu" in notice and "12/345" in notice


def test_redirect_without_letter_number(app_engine, world):
    bot = StaffBot(app_engine)
    appeal = triaged(bot, world)
    bot.press(callback(REDIRECT, appeal.id, appeal.version), user=COORDINATOR_TG)
    bot.say("DSMF", user=COORDINATOR_TG)
    bot.press(callback(SKIP_LETTER, appeal.id, appeal.version), user=COORDINATOR_TG)
    assert bot.appeal(appeal.id).status == AppealStatus.redirected


def test_not_review_with_legal_reason(app_engine, world):
    bot = StaffBot(app_engine)
    appeal = triaged(bot, world)
    reasons, _ = bot.press(callback(NOT_REVIEW, appeal.id, appeal.version), user=COORDINATOR_TG)
    assert callback(NOT_REVIEW_REASON, appeal.id, appeal.version, "3") in buttons(reasons)
    bot.press(callback(NOT_REVIEW_REASON, appeal.id, appeal.version, "3"), user=COORDINATOR_TG)
    bot.say("Məhkəmənin 01.09.2026 tarixli qərarı var", user=COORDINATOR_TG)
    row = bot.appeal(appeal.id)
    assert row.status == AppealStatus.not_reviewed and "8.1.3" in row.not_reviewed_reason


def test_extend_due_date(app_engine, world):
    bot = StaffBot(app_engine)
    appeal = triaged(bot, world)
    old_due = appeal.due_date
    bot.press(callback(EXTEND_DAYS, appeal.id, appeal.version, 10), user=COORDINATOR_TG)
    bot.say("Bankdan cavab gözlənilir", user=COORDINATOR_TG)
    assert bot.appeal(appeal.id).due_date > old_due


# ---------- admin və panel ----------
def test_deactivation_returns_open_appeals_to_coordinators(app_engine, world):
    bot = StaffBot(app_engine)
    appeal = assigned(bot, world)
    listing = bot.say("/emekdaslar", user=COORDINATOR_TG)
    assert f"{DEACTIVATE}:{world.staff_a.id}" in buttons(listing)
    assert f"{DEACTIVATE}:{world.coordinator.id}" not in buttons(listing)  # özünü yox
    bot.press(f"{DEACTIVATE}:{world.staff_a.id}", user=COORDINATOR_TG)
    with bot.factory() as s:
        assert s.get(Staff, world.staff_a.id).is_active is False
    assert bot.appeal(appeal.id).status == AppealStatus.new


def test_panel_counts(app_engine, world):
    bot = StaffBot(app_engine)
    triaged(bot, world)
    triaged(bot, world, priority=Priority.urgent)
    assigned(bot, world)
    panel = texts(bot.say("/panel", user=HEAD_TG))
    assert "Yeni (təyin olunmayıb): 2" in panel and "İcrada: 1" in panel and "Təcili: 1" in panel
    assert "Bu gün daxil olub: 3" in panel
    assert "yalnız" in texts(bot.say("/panel", user=STAFF_A_TG))


def test_my_work_lists_only_my_open_appeals(app_engine, world):
    bot = StaffBot(app_engine)
    mine = assigned(bot, world)
    other = triaged(bot, world)
    work = texts(bot.say("/islerim", user=STAFF_A_TG))
    assert mine.number in work and other.number not in work


def test_stale_multi_step_button_is_refused_before_typing(app_engine, world):
    bot = StaffBot(app_engine)
    appeal = assigned(bot, world)
    messages, answer = bot.press(callback(ANSWER, appeal.id, appeal.version - 1), user=STAFF_A_TG)
    assert "köhnəlib" in answer["text"] and messages == []


def test_forged_admin_buttons_are_refused(app_engine, world):
    bot = StaffBot(app_engine)
    _, invite = bot.press(f"{INVITE_ROLE}:coordinator", user=DEPUTY_TG)
    _, deactivate = bot.press(f"{DEACTIVATE}:{world.staff_a.id}", user=DEPUTY_TG)
    assert "icazə" in invite["text"].lower() and "icazə" in deactivate["text"].lower()
    with bot.factory() as s:
        from app.db.tables import StaffInvite
        assert s.scalar(select(StaffInvite.id)) is None and s.get(Staff, world.staff_a.id).is_active


def test_first_admin_from_the_command_line_invite(app_engine):
    # Yeni bazada əməkdaş yoxdur: ilk admin "python -m app.manage invite coordinator --admin" ilə gəlir
    from app.staff.invites import create_invite
    bot = StaffBot(app_engine)
    with bot.factory() as s, s.begin():
        token = create_invite(s, role=StaffRole.coordinator, department_id=None, is_admin=True,
                              created_by_staff_id=None, now=MONDAY_10)
    bot.say(f"/start inv_{token}", user=STRANGER_TG, first_name="Admin", last_name="Koordinator")
    with bot.factory() as s:
        admin = s.scalar(select(Staff).where(Staff.telegram_user_id == STRANGER_TG))
        assert admin.is_admin and admin.role == StaffRole.coordinator
    assert "rolunu seçin" in texts(bot.say("/devet", user=STRANGER_TG))



def test_preview_of_an_unfinished_draft_has_no_send_button(app_engine, world):
    bot = StaffBot(app_engine)
    appeal = assigned(bot, world)
    preview = write_answer(bot, appeal, Outcome.satisfied, "Hörmətli vətəndaş, ödənişin səbəbi [...] olub.")
    assert "🔴" in texts(preview) and "[...]" in texts(preview)
    assert callback(SEND, appeal.id, appeal.version) not in buttons(preview)


# ---------- vətəndaşın əlavələri ----------
def with_attachment(bot: StaffBot, appeal: Appeal) -> None:
    from app.db.tables import Attachment

    with bot.factory() as s, s.begin():
        s.add(Attachment(owner_telegram_id=5001, appeal_id=appeal.id, kind="photo", mime="image/jpeg", size=4,
                         name_enc=CRYPTO.encrypt("photo_1.jpg"), content_enc=CRYPTO.encrypt_bytes(b"JPEG-ab")))


def test_card_shows_attachments_and_assignee_receives_the_files(app_engine, world):
    from app.notifier.cards import ATTACHMENTS

    bot = StaffBot(app_engine)
    appeal = triaged(bot, world)
    with_attachment(bot, appeal)
    appeal = bot.service(lambda svc: svc.assign(world.coordinator, appeal.id, world.staff_a.id,
                                                expected_version=appeal.version))
    card = [p for m, chat, p in bot.staff_tg.sent if m == "send_message" and chat == STAFF_A_TG][-1]
    assert "📎 Əlavələr: 1" in card["text"] and callback(ATTACHMENTS, appeal.id) in buttons([card])

    bot.press(callback(ATTACHMENTS, appeal.id), user=STAFF_A_TG)
    files = [(chat, p["filename"], p["content"], p["caption"]) for m, chat, p in bot.staff_tg.sent
             if m == "send_document"]
    assert files == [(STAFF_A_TG, "photo_1.jpg", b"JPEG-ab", appeal.number)]
    with bot.factory() as s:
        events = [e.payload for e in s.scalars(select(AppealEvent).where(AppealEvent.action == "pii_viewed"))]
    assert events == [{"field": "attachments", "count": 1}]


def test_other_staff_cannot_get_the_files(app_engine, world):
    from app.notifier.cards import ATTACHMENTS

    bot = StaffBot(app_engine)
    appeal = assigned(bot, world)
    with_attachment(bot, appeal)
    _, answer = bot.press(callback(ATTACHMENTS, appeal.id), user=STAFF_B_TG)
    assert "icazəniz yoxdur" in answer["text"]
    assert not [m for m, _, _ in bot.staff_tg.sent if m == "send_document"]
