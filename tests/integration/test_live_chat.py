"""Canlı çat: vətəndaş botu ↔ əməkdaş botu, real PostgreSQL, saxta Telegram (iki bot bir worker-lə)."""
import itertools
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.agents.citizen_graph import CitizenTurn
from app.citizen.controller import BTN_OPERATOR, CitizenController
from app.citizen.session import MemoryLimiter, MemorySessionStore
from app.db.tables import ChatLog, LiveChat
from app.livechat import IDLE_MINUTES, WAIT_MINUTES, LiveChats
from app.notifier.cards import LIVE_CHAT_ACCEPT, callback
from app.notifier.outbox import OutboxWorker
from app.staff.controller import StaffController
from app.staff.session import StaffSession
from app.workflow.states import BotKind
from tests.fakes import FakeTelegramClient
from tests.integration.helpers import (
    CAL, CFG, CITIZEN_TG, COORDINATOR_TG, CRYPTO, DEPUTY_TG, MONDAY_10, ORG, STAFF_A_TG, Clock,
)

pytestmark = pytest.mark.integration
T = CFG.templates.texts
HUMAN = CitizenTurn(intent="human", reply="Rəsmi müraciət kimi qeydə ala bilərəm.", next_step="idle",
                    buttons=["start_appeal"])


class TwoBots:
    def __init__(self, app_engine, *turns):
        self.clock = Clock(MONDAY_10)
        self.factory = sessionmaker(app_engine, expire_on_commit=False)
        self.citizen_tg, self.staff_tg = FakeTelegramClient(), FakeTelegramClient(first_message_id=9000)
        self.worker = OutboxWorker(self.factory, {BotKind.citizen: self.citizen_tg, BotKind.staff: self.staff_tg},
                                   config=CFG.sla.outbox, clock=self.clock)
        self.turns = list(turns)
        self.citizen = CitizenController(
            self.factory, store=MemorySessionStore(), limiter=MemoryLimiter(), turn=lambda **kw: self.turns.pop(0),
            client=self.citizen_tg, config=CFG, calendar=CAL, crypto=CRYPTO, org_name=ORG,
            hotline="Qaynar xətt: 142", rate_limit=100, clock=self.clock)
        self.staff = StaffController(
            self.factory, store=MemorySessionStore(StaffSession), limiter=MemoryLimiter(), client=self.staff_tg,
            config=CFG, calendar=CAL, crypto=CRYPTO, org_name=ORG, qa=lambda **kw: None, bot_username="b",
            clock=self.clock, hotline="Qaynar xətt: 142")
        self._ids = itertools.count(1)

    def _run(self, controller, update) -> None:
        controller.handle(update)
        while self.worker.process_batch():
            pass

    def citizen_says(self, text: str) -> None:
        n = next(self._ids)
        self._run(self.citizen, {"update_id": n, "message": {"message_id": n, "from": {"id": CITIZEN_TG},
                                                             "chat": {"id": CITIZEN_TG, "type": "private"}, "text": text}})

    def citizen_presses(self, data: str) -> None:
        n = next(self._ids)
        self._run(self.citizen, {"update_id": n, "callback_query": {
            "id": f"c{n}", "from": {"id": CITIZEN_TG}, "data": data,
            "message": {"message_id": 1, "chat": {"id": CITIZEN_TG, "type": "private"}}}})

    def staff_says(self, text: str, user: int) -> None:
        n = next(self._ids)
        self._run(self.staff, {"update_id": n, "message": {"message_id": n, "from": {"id": user, "first_name": "X"},
                                                           "chat": {"id": user, "type": "private"}, "text": text}})

    def staff_presses(self, data: str, user: int) -> str:
        n = next(self._ids)
        self._run(self.staff, {"update_id": n, "callback_query": {
            "id": f"s{n}", "from": {"id": user, "first_name": "X"}, "data": data,
            "message": {"message_id": 1, "chat": {"id": user, "type": "private"}}}})
        return [p for m, _, p in self.staff_tg.sent if m == "answer_callback_query" and p["id"] == f"s{n}"][0]["text"]

    def to_citizen(self) -> list[str]:
        return [p["text"] for m, chat, p in self.citizen_tg.sent if m == "send_message" and chat == CITIZEN_TG]

    def to_staff(self, user: int) -> list[dict]:
        return [p for m, chat, p in self.staff_tg.sent if m == "send_message" and chat == user]

    def chat(self) -> LiveChat:
        with self.factory() as s:
            return s.scalar(select(LiveChat).order_by(LiveChat.id.desc()))


def started(app_engine) -> TwoBots:
    bots = TwoBots(app_engine, HUMAN)
    bots.citizen_says("Operatorla danışmaq istəyirəm")
    bots.citizen_presses(BTN_OPERATOR)
    return bots


def test_full_live_chat_between_citizen_and_coordinator(app_engine, world):
    bots = TwoBots(app_engine, HUMAN)
    bots.citizen_says("Canlı adamla danışmaq istəyirəm")
    offer = [p for m, chat, p in bots.citizen_tg.sent if m == "send_message"][-1]
    assert offer["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == BTN_OPERATOR  # iş vaxtıdır
    assert offer["text"] == T["human_handoff_live"]  # mətn də operatoru təklif edir

    bots.citizen_presses(BTN_OPERATOR)
    assert bots.to_citizen()[-1] == T["live_chat_waiting"]
    chat = bots.chat()
    cards = [p for p in bots.to_staff(COORDINATOR_TG) if "Canlı çat sorğusu" in p["text"]]
    assert cards and bots.to_staff(DEPUTY_TG)  # bütün aktiv koordinatorlara
    bots.citizen_says("Pensiyam niyə azalıb?")  # gözləyərkən

    assert bots.staff_presses(callback(LIVE_CHAT_ACCEPT, chat.id), COORDINATOR_TG) == "Çat qəbul edildi."
    assert "Pensiyam niyə azalıb?" in bots.to_staff(COORDINATOR_TG)[-1]["text"]  # gözləmədə yazılanlar
    assert bots.to_citizen()[-1] == T["live_chat_connected"]
    assert "artıq" in bots.staff_presses(callback(LIVE_CHAT_ACCEPT, chat.id), DEPUTY_TG)  # ikinci qəbul yoxdur

    bots.citizen_says("Ötən aydan azdır")
    assert bots.to_staff(COORDINATOR_TG)[-1]["text"] == f"💬 Vətəndaş (#{chat.id}): Ötən aydan azdır"
    bots.staff_says("İndeksasiya ilə bağlıdır, yoxlayıram.", COORDINATOR_TG)
    assert bots.to_citizen()[-1] == "👤 Operator: İndeksasiya ilə bağlıdır, yoxlayıram."
    bots.staff_says("/bitir", COORDINATOR_TG)
    assert bots.chat().status == "closed" and bots.chat().closed_by == "staff"
    assert bots.to_citizen()[-1] == T["live_chat_closed"]

    with bots.factory() as s:
        logged = [(c.role, c.content) for c in s.scalars(select(ChatLog).where(ChatLog.intent == "live_chat")
                                                         .order_by(ChatLog.id))]
    assert logged == [("citizen", "Pensiyam niyə azalıb?"), ("citizen", "Ötən aydan azdır"),
                      ("operator", "İndeksasiya ilə bağlıdır, yoxlayıram.")]


def test_citizen_can_end_the_chat(app_engine, world):
    bots = started(app_engine)
    bots.staff_presses(callback(LIVE_CHAT_ACCEPT, bots.chat().id), COORDINATOR_TG)
    bots.citizen_says("/bitir")
    assert bots.chat().closed_by == "citizen" and "vətəndaş bağladı" in bots.to_staff(COORDINATOR_TG)[-1]["text"]
    bots.staff_says("salam", COORDINATOR_TG)  # çat bitib: mətn vətəndaşa getmir
    assert bots.to_citizen()[-1] == T["live_chat_closed"]


def test_unaccepted_chat_expires_and_offers_the_hotline(app_engine, world):
    bots = started(app_engine)
    bots.clock.moment = MONDAY_10 + timedelta(minutes=WAIT_MINUTES + 1)
    with bots.factory() as s, s.begin():
        assert LiveChats(s, now=bots.clock.moment, templates=CFG.templates, hotline="142").sweep() == \
            {"live_chats_expired": 1, "live_chats_closed": 0}
    bots.worker.process_batch()
    assert bots.chat().status == "expired" and "operatorlar məşğuldur" in bots.to_citizen()[-1]
    assert "bitib" in bots.staff_presses(callback(LIVE_CHAT_ACCEPT, bots.chat().id), COORDINATOR_TG)


def test_idle_active_chat_is_closed(app_engine, world):
    bots = started(app_engine)
    bots.staff_presses(callback(LIVE_CHAT_ACCEPT, bots.chat().id), COORDINATOR_TG)
    bots.clock.moment = MONDAY_10 + timedelta(minutes=IDLE_MINUTES + 1)
    with bots.factory() as s, s.begin():
        assert LiveChats(s, now=bots.clock.moment, templates=CFG.templates).sweep()["live_chats_closed"] == 1
    assert bots.chat().closed_by == "timeout"


def test_no_operator_outside_work_hours_and_only_coordinators_accept(app_engine, world):
    bots = TwoBots(app_engine)
    bots.clock.moment = MONDAY_10.replace(hour=21)
    bots.citizen_presses(BTN_OPERATOR)
    assert "iş vaxtı" in bots.to_citizen()[-1] and bots.chat() is None

    bots.clock.moment = MONDAY_10
    bots.citizen_says("/operator")
    assert "icazə" in bots.staff_presses(callback(LIVE_CHAT_ACCEPT, bots.chat().id), STAFF_A_TG)
    assert bots.chat().status == "waiting"


def test_files_are_not_silently_kept_during_a_live_chat(app_engine, world):
    from app.db.tables import Attachment

    bots = started(app_engine)
    n = 999
    bots._run(bots.citizen, {"update_id": n, "message": {"message_id": n, "from": {"id": CITIZEN_TG},
                                                         "chat": {"id": CITIZEN_TG, "type": "private"},
                                                         "photo": [{"file_id": "P1", "file_size": 10}]}})
    assert bots.to_citizen()[-1] == T["live_chat_text_only"]
    with bots.factory() as s:
        assert s.scalar(select(Attachment)) is None
