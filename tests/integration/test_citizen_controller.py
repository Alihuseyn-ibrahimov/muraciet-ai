"""Vətəndaş botunun kontrolleri: hazır Telegram update-ləri → baza, outbox → saxta Telegram (spec 6-cı bölmə).

Dialoq qrafı əksər testlərdə ssenari ilə əvəz olunur (o, ayrıca test olunub). LLM-in düşdüyü test
real qrafı xəta verən saxta modellərlə işlədir.
"""
import itertools
from functools import partial

import pytest
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.citizen_graph import (
    BTN_START_APPEAL, CitizenAgents, CitizenTurn, build_citizen_graph, run_turn,
)
from app.agents.faq import FaqAgent
from app.agents.guard import GuardAgent
from app.agents.intake import IntakeAgent
from app.agents.router import RouterAgent
from app.citizen.controller import (
    APPEAL_SEND, CONSENT_NO, CONSENT_YES, PROFILE_UPDATE, PROFILE_YES, CitizenController,
)
from app.citizen.session import MemoryLimiter, MemorySessionStore
from app.db.tables import Appeal, ChatLog, Citizen, Job, TelegramMessage
from app.notifier.cards import FEEDBACK_NEG, FEEDBACK_POS, callback
from app.notifier.notifier import CITIZEN_QUESTION, OutboxNotifier
from app.notifier.outbox import OutboxWorker
from app.privacy.llm import PrivateLLM
from app.workflow.service import AppealService, RoutingOutcome, TriageOutcome
from app.workflow.states import AppealStatus, BotKind, Category, Outcome, Priority
from tests.fakes import FakeChatModel, FakeTelegramClient
from tests.integration.helpers import CAL, CFG, CITIZEN_TG, CRYPTO, MONDAY_10, ORG, OTHER_CITIZEN_TG, Clock

pytestmark = pytest.mark.integration
T = CFG.templates.texts
NEW_TG = 6001
FORM = {"subject": "Pensiya köçmür", "summary": "Pensiya 3 aydır köçmür", "description": "Pensiyam 3 aydır köçmür"}


def start_form_turn() -> CitizenTurn:
    return CitizenTurn(intent="new_appeal", reply=None, next_step="form", effects=[{"type": "start_form", **FORM}])


class ScriptedTurn:
    """Dialoq qrafının yerinə: növbəti hazır cavabı qaytarır və çağırışları yadda saxlayır."""

    def __init__(self, *turns: CitizenTurn):
        self.turns, self.calls = list(turns), []

    def __call__(self, **kwargs) -> CitizenTurn:
        self.calls.append(kwargs)
        return self.turns.pop(0)


class Bot:
    def __init__(self, app_engine, turn):
        self.factory = sessionmaker(app_engine, expire_on_commit=False)
        self.telegram = FakeTelegramClient(first_message_id=5000)
        self.worker = OutboxWorker(self.factory, {BotKind.citizen: self.telegram}, config=CFG.sla.outbox,
                                   clock=Clock(MONDAY_10))
        self.turn = turn
        self.controller = CitizenController(
            self.factory, store=MemorySessionStore(), limiter=MemoryLimiter(), turn=turn, client=self.telegram,
            config=CFG, calendar=CAL, crypto=CRYPTO, org_name=ORG, hotline="", rate_limit=100,
            clock=Clock(MONDAY_10))
        self._update_ids, self._message_ids = itertools.count(1), itertools.count(100)

    def _handle(self, update: dict, user: int) -> list[dict]:
        seen = len(self.telegram.sent)
        self.controller.handle(update)
        while self.worker.process_batch():
            pass
        return [payload for method, chat, payload in self.telegram.sent[seen:]
                if method == "send_message" and chat == user]

    def say(self, text: str | None = None, *, user: int = NEW_TG, reply_to: int | None = None,
            contact: dict | None = None, chat_type: str = "private", extra: dict | None = None) -> list[dict]:
        message = {"message_id": next(self._message_ids), "from": {"id": user}, "chat": {"id": user, "type": chat_type},
                   "date": 0}
        if text is not None:
            message["text"] = text
        if reply_to is not None:
            message["reply_to_message"] = {"message_id": reply_to}
        if contact is not None:
            message["contact"] = contact
        message.update(extra or {})
        return self._handle({"update_id": next(self._update_ids), "message": message}, user)

    def press(self, data: str, *, user: int = NEW_TG) -> list[dict]:
        update_id = next(self._update_ids)
        query = {"id": f"cb{update_id}", "from": {"id": user}, "data": data,
                 "message": {"message_id": 1, "chat": {"id": user, "type": "private"}}}
        return self._handle({"update_id": update_id, "callback_query": query}, user)

    def share_contact(self, phone: str = "+994 50 111 22 33", *, user: int = NEW_TG, owner: int | None = None):
        return self.say(user=user, contact={"phone_number": phone, "user_id": owner or user})

    def service(self, fn):
        with self.factory() as s, s.begin():
            notifier = OutboxNotifier(s, config=CFG, crypto=CRYPTO, org_name=ORG, clock=Clock(MONDAY_10))
            result = fn(AppealService(s, config=CFG, calendar=CAL, crypto=CRYPTO, notifier=notifier,
                                      clock=Clock(MONDAY_10)), s)
        while self.worker.process_batch():
            pass
        return result


def texts(messages: list[dict]) -> str:
    return "\n".join(m["text"] for m in messages)


def buttons(messages: list[dict]) -> set[str]:
    return {b.get("callback_data") for m in messages for row in (m.get("reply_markup") or {}).get("inline_keyboard", [])
            for b in row}


def fill_new_profile(bot: Bot) -> list[dict]:
    bot.say("Pensiyam 3 aydır köçmür")
    bot.press(CONSENT_YES)
    bot.share_contact()
    bot.say("Həsənov Kamran Rauf oğlu")
    bot.say("7ab12cd")
    return bot.say("Bakı, Nəsimi r., Azadlıq pr. 10, m. 5")


# ---------- ilk müraciət ----------
def test_first_appeal_full_registration(app_engine):
    bot = Bot(app_engine, ScriptedTurn(start_form_turn()))
    consent = bot.say("Pensiyam 3 aydır köçmür")
    assert T["consent_prompt"] in texts(consent) and buttons(consent) == {CONSENT_YES, CONSENT_NO}
    assert "Fərdi məlumatların emalına razılıq" in texts(consent) and "**" not in texts(consent)
    assert ORG in texts(consent) and "{org_name}" not in texts(consent)

    phone = bot.press(CONSENT_YES)
    assert texts(phone) == T["ask_phone"]
    assert phone[0]["reply_markup"]["keyboard"][0][0]["request_contact"] is True

    name = bot.share_contact()
    assert texts(name) == T["ask_name"] and name[0]["reply_markup"] == {"remove_keyboard": True}
    assert texts(bot.say("Həsənov Kamran Rauf oğlu")) == T["ask_fin"]
    assert texts(bot.say("7ab12cd")) == T["ask_address"]

    confirm = bot.say("Bakı, Nəsimi r., Azadlıq pr. 10, m. 5")
    assert "Pensiya köçmür" in texts(confirm) and "7AB***D" in texts(confirm) and "7AB12CD" not in texts(confirm)
    assert APPEAL_SEND in buttons(confirm)

    done = bot.press(APPEAL_SEND)
    assert "MR-2026-" in texts(done) and "Cavab bu çata gələcək" in texts(done)
    with Session(app_engine) as s:
        citizen = s.scalar(select(Citizen).where(Citizen.telegram_user_id == NEW_TG))
        assert (citizen.last_name, citizen.first_name, citizen.patronymic) == ("Həsənov", "Kamran", "Rauf oğlu")
        assert CRYPTO.decrypt(citizen.fin_enc) == "7AB12CD" and CRYPTO.decrypt(citizen.phone_enc) == "+994501112233"
        assert citizen.consent_version == CFG.consent.version and citizen.consent_at is not None
        appeal = s.scalar(select(Appeal).where(Appeal.citizen_id == citizen.id))
        assert appeal.subject == FORM["subject"] and appeal.description == FORM["description"]
        assert s.scalar(select(func.count()).select_from(Job).where(Job.kind == "triage")) == 1
        logged = " ".join(s.scalars(select(ChatLog.content)))
    # forma sahələri chat_logs-a açıq yazılmır
    for secret in ("7AB12CD", "7ab12cd", "Azadlıq pr. 10", "Həsənov Kamran", "111 22 33"):
        assert secret not in logged, secret
    assert "Pensiyam 3 aydır köçmür" in logged


def test_invalid_fin_is_asked_again(app_engine):
    bot = Bot(app_engine, ScriptedTurn(start_form_turn()))
    bot.say("Pensiyam köçmür")
    bot.press(CONSENT_YES)
    bot.share_contact()
    bot.say("Həsənov Kamran Rauf oğlu")
    assert texts(bot.say("12345")) == T["invalid_fin"]
    assert texts(bot.say("ABCDEFGH")) == T["invalid_fin"]
    assert texts(bot.say(" 7ab 12cd ")) == T["ask_address"]


def test_invalid_name_is_asked_again(app_engine):
    bot = Bot(app_engine, ScriptedTurn(start_form_turn()))
    bot.say("Pensiyam köçmür")
    bot.press(CONSENT_YES)
    bot.share_contact()
    assert texts(bot.say("Kamran")) == T["invalid_name"]
    assert texts(bot.say("Həsənov Kamran 123")) == T["invalid_name"]
    assert texts(bot.say("Həsənov Kamran Rauf oğlu")) == T["ask_fin"]


def test_someone_elses_contact_is_rejected(app_engine):
    bot = Bot(app_engine, ScriptedTurn(start_form_turn()))
    bot.say("Pensiyam köçmür")
    bot.press(CONSENT_YES)
    assert texts(bot.share_contact(owner=999)) == T["contact_not_own"]
    assert texts(bot.say("+994501112233")) == T["ask_phone"]  # yazılmış nömrə də qəbul olunmur
    assert texts(bot.share_contact()) == T["ask_name"]


def test_declined_consent_keeps_answering_questions(app_engine):
    question = CitizenTurn(intent="question", reply="Pensiya ayın 8-15-də köçürülür.", next_step="idle")
    bot = Bot(app_engine, ScriptedTurn(start_form_turn(), question))
    bot.say("Pensiyam köçmür")
    assert texts(bot.press(CONSENT_NO)) == T["consent_declined"]
    with Session(app_engine) as s:
        assert s.scalar(select(func.count()).select_from(Appeal)) == 0
    assert "8-15" in texts(bot.say("Pensiya nə vaxt köçür?"))
    assert bot.turn.calls[-1]["step"] == "idle"


# ---------- qayıdan vətəndaş ----------
def test_returning_citizen_confirms_profile(app_engine, world):
    bot = Bot(app_engine, ScriptedTurn(start_form_turn()))
    profile = bot.say("Pensiyam köçmür", user=CITIZEN_TG)
    assert "5AB***D" in texts(profile) and "5ABC12D" not in texts(profile)
    assert buttons(profile) == {PROFILE_YES, PROFILE_UPDATE}
    confirm = bot.press(PROFILE_YES, user=CITIZEN_TG)
    assert APPEAL_SEND in buttons(confirm)
    assert "MR-2026-" in texts(bot.press(APPEAL_SEND, user=CITIZEN_TG))


def test_returning_citizen_can_update_profile(app_engine, world):
    bot = Bot(app_engine, ScriptedTurn(start_form_turn()))
    bot.say("Pensiyam köçmür", user=CITIZEN_TG)
    assert texts(bot.press(PROFILE_UPDATE, user=CITIZEN_TG)) == T["ask_phone"]
    bot.share_contact("0559998877", user=CITIZEN_TG)
    bot.say("Məmmədov Əli Vəli oğlu", user=CITIZEN_TG)
    bot.say("5ABC12D", user=CITIZEN_TG)
    confirm = bot.say("Bakı, Yeni ünvan küçəsi 7", user=CITIZEN_TG)
    assert APPEAL_SEND in buttons(confirm) and "Yeni ünvan" in texts(confirm)
    with Session(app_engine) as s:
        citizen = s.get(Citizen, world.citizen_id)
        assert CRYPTO.decrypt(citizen.phone_enc) == "+994559998877"


# ---------- gözləmə dövrü ----------
def new_appeal(bot: Bot, citizen_id: int, subject: str = "Pensiya") -> Appeal:
    def create(svc, s):
        appeal = svc.create_appeal(citizen_id, subject=subject, summary="Pensiya köçmür", description="Köçmür")
        svc.apply_triage(appeal.id, TriageOutcome(Category.request, Priority.normal, False),
                         RoutingOutcome("pensiya", "pensiya"))
        return appeal
    return bot.service(create)


def waiting_appeal(bot: Bot, world, citizen_id: int, question: str = "Hansı bankdır?") -> Appeal:
    appeal = new_appeal(bot, citizen_id)
    appeal = bot.service(lambda svc, s: svc.assign(world.coordinator, appeal.id, world.staff_a.id,
                                                   expected_version=appeal.version))
    return bot.service(lambda svc, s: svc.ask_citizen(world.staff_a, appeal.id, question,
                                                      expected_version=appeal.version))


def test_status_shows_only_own_appeals(app_engine, world):
    bot = Bot(app_engine, ScriptedTurn(CitizenTurn(intent="status", reply=None, next_step="idle",
                                                   effects=[{"type": "show_status"}])))
    mine = new_appeal(bot, world.citizen_id, "Mənim müraciətim")
    other = new_appeal(bot, world.other_citizen_id, "Başqasının müraciəti")
    status = texts(bot.say("Müraciətim nə yerdədir?", user=CITIZEN_TG))
    assert mine.number in status and "Qeydə alınıb" in status
    assert other.number not in status and "Başqasının" not in status


def test_reply_to_staff_question_is_bound_without_the_dialogue_graph(app_engine, world):
    bot = Bot(app_engine, ScriptedTurn())
    appeal = waiting_appeal(bot, world, world.citizen_id)
    with Session(app_engine) as s:
        question = s.scalar(select(TelegramMessage).where(TelegramMessage.purpose == CITIZEN_QUESTION))
    answer = bot.say("Kapital Bank", user=CITIZEN_TG, reply_to=question.message_id)
    assert texts(answer) == CFG.templates.render("reply_forwarded", number=appeal.number)
    assert bot.turn.calls == []
    with Session(app_engine) as s:
        assert s.get(Appeal, appeal.id).status == AppealStatus.assigned


def clarification_turn() -> CitizenTurn:
    return CitizenTurn(intent="clarification_reply", reply=None, next_step="idle",
                       effects=[{"type": "forward_clarification", "text": "Kapital Bank"}])


def test_clarification_goes_to_the_only_waiting_appeal(app_engine, world):
    bot = Bot(app_engine, ScriptedTurn(clarification_turn()))
    appeal = waiting_appeal(bot, world, world.citizen_id)
    assert appeal.number in texts(bot.say("Kapital Bank", user=CITIZEN_TG))
    assert bot.turn.calls[0]["has_open_question"] is True
    with Session(app_engine) as s:
        assert s.get(Appeal, appeal.id).status == AppealStatus.assigned


def test_clarification_with_several_waiting_appeals_asks_which(app_engine, world):
    bot = Bot(app_engine, ScriptedTurn(clarification_turn()))
    first = waiting_appeal(bot, world, world.citizen_id)
    second = waiting_appeal(bot, world, world.citizen_id, "Hansı aydan?")
    choice = bot.say("Kapital Bank", user=CITIZEN_TG)
    assert texts(choice) == T["choose_appeal"]
    assert buttons(choice) == {f"pick:{first.id}", f"pick:{second.id}"}
    assert second.number in texts(bot.press(f"pick:{second.id}", user=CITIZEN_TG))
    with Session(app_engine) as s:
        assert s.get(Appeal, second.id).status == AppealStatus.assigned
        assert s.get(Appeal, first.id).status == AppealStatus.waiting_citizen


# ---------- cavab və rəy ----------
def answered_appeal(bot: Bot, world) -> Appeal:
    appeal = new_appeal(bot, world.citizen_id)
    appeal = bot.service(lambda svc, s: svc.assign(world.coordinator, appeal.id, world.staff_a.id,
                                                   expected_version=appeal.version))
    return bot.service(lambda svc, s: svc.submit_answer(
        world.staff_a, appeal.id, outcome=Outcome.satisfied, text="Pensiyanız 24.10.2026 tarixində köçürüldü.",
        qa_critical=False, expected_version=appeal.version))


def test_negative_feedback_reopens_the_appeal(app_engine, world):
    bot = Bot(app_engine, ScriptedTurn())
    appeal = answered_appeal(bot, world)
    assert appeal.status == AppealStatus.answered
    assert texts(bot.press(callback(FEEDBACK_NEG, appeal.id), user=CITIZEN_TG)) == T["feedback_reason_prompt"]
    reopened = bot.say("Pul hələ də gəlməyib", user=CITIZEN_TG)
    assert CFG.templates.render("reopened", number=appeal.number) in texts(reopened)
    assert bot.turn.calls == []  # səbəb mətni dialoq qrafına getmir
    with Session(app_engine) as s:
        row = s.get(Appeal, appeal.id)
        assert (row.status, row.reopen_count) == (AppealStatus.new, 1)


def test_positive_feedback_closes_and_a_second_press_is_harmless(app_engine, world):
    bot = Bot(app_engine, ScriptedTurn())
    appeal = answered_appeal(bot, world)
    assert texts(bot.press(callback(FEEDBACK_POS, appeal.id), user=CITIZEN_TG)) == T["feedback_thanks"]
    assert bot.press(callback(FEEDBACK_POS, appeal.id), user=CITIZEN_TG) == []  # yalnız callback cavabı
    with Session(app_engine) as s:
        assert s.get(Appeal, appeal.id).status == AppealStatus.closed


def test_other_citizen_cannot_rate_my_appeal(app_engine, world):
    bot = Bot(app_engine, ScriptedTurn())
    appeal = answered_appeal(bot, world)
    bot.press(callback(FEEDBACK_POS, appeal.id), user=OTHER_CITIZEN_TG)
    with Session(app_engine) as s:
        assert s.get(Appeal, appeal.id).status == AppealStatus.answered


# ---------- əmrlər və qoruyucular ----------
def test_my_data_shows_masked_profile(app_engine, world):
    bot = Bot(app_engine, ScriptedTurn())
    shown = texts(bot.say("/melumatlarim", user=CITIZEN_TG))
    assert "5AB***D" in shown and "+99450***4567" in shown
    assert "5ABC12D" not in shown and "+994501234567" not in shown
    assert texts(bot.say("/melumatlarim")) == T["no_profile"]


def test_start_resets_an_unfinished_form(app_engine):
    question = CitizenTurn(intent="question", reply="Cavab", next_step="idle")
    bot = Bot(app_engine, ScriptedTurn(start_form_turn(), question))
    bot.say("Pensiyam köçmür")
    assert ORG in texts(bot.say("/start"))
    bot.say("Sual")
    assert bot.turn.calls[-1]["step"] == "idle"


def test_duplicate_update_is_processed_once(app_engine):
    bot = Bot(app_engine, ScriptedTurn(CitizenTurn(intent="greeting", reply="Salam!", next_step="idle")))
    update = {"update_id": 77, "message": {"message_id": 1, "from": {"id": NEW_TG},
                                           "chat": {"id": NEW_TG, "type": "private"}, "date": 0, "text": "Salam"}}
    assert texts(bot._handle(update, NEW_TG)) == "Salam!"
    assert bot._handle(update, NEW_TG) == []


def test_group_chats_are_ignored(app_engine):
    bot = Bot(app_engine, ScriptedTurn())
    assert bot.say("Salam", chat_type="group") == [] and bot.turn.calls == []


def test_rate_limit(app_engine):
    bot = Bot(app_engine, ScriptedTurn(*[CitizenTurn(intent="greeting", reply="Salam!", next_step="idle")] * 3))
    bot.controller.rate_limit = 3
    for _ in range(3):
        bot.say("Salam")
    assert texts(bot.say("Salam")) == T["rate_limited"]
    assert bot.say("Salam") == []  # xəbərdarlıq bir dəfə


def test_callbacks_are_answered_immediately(app_engine, world):
    bot = Bot(app_engine, ScriptedTurn())
    bot.press(callback(FEEDBACK_POS, 999999), user=CITIZEN_TG)
    assert [m for m, _, _ in bot.telegram.sent] == ["answer_callback_query"]


# ---------- LLM düşəndə ----------
def test_registration_still_works_when_the_llm_is_down(app_engine, world):
    def broken(agent):
        return PrivateLLM(agent, model=FakeChatModel(script=[RuntimeError("API")] * 10))
    agents = CitizenAgents(GuardAgent(broken("guard")), RouterAgent(broken("router")), FaqAgent(broken("faq")),
                           IntakeAgent(broken("intake")))
    graph = build_citizen_graph(agents, search=lambda q: [], templates=CFG.templates, org_name=ORG, hotline="",
                                checkpointer=MemorySaver())
    bot = Bot(app_engine, partial(run_turn, graph))
    offer = bot.say("Pensiyam 3 aydır köçmür", user=CITIZEN_TG)
    assert BTN_START_APPEAL in buttons(offer)
    profile = bot.press(BTN_START_APPEAL, user=CITIZEN_TG)  # qəbul agenti düşüb: vətəndaşın mətni ilə davam
    assert PROFILE_YES in buttons(profile)
    bot.press(PROFILE_YES, user=CITIZEN_TG)
    assert "MR-2026-" in texts(bot.press(APPEAL_SEND, user=CITIZEN_TG))
    with Session(app_engine) as s:
        assert s.scalar(select(Appeal.description)) == "Pensiyam 3 aydır köçmür"


def test_forged_pick_button_cannot_answer_someone_elses_question(app_engine, world):
    bot = Bot(app_engine, ScriptedTurn(clarification_turn()))
    mine = waiting_appeal(bot, world, world.citizen_id)
    waiting_appeal(bot, world, world.other_citizen_id)
    waiting_appeal(bot, world, world.other_citizen_id, "Hansı aydan?")
    bot.say("Kapital Bank", user=OTHER_CITIZEN_TG)  # seçim ekranı
    forged = bot.press(f"pick:{mine.id}", user=OTHER_CITIZEN_TG)
    assert texts(forged) == CFG.templates.render("question_closed", number=mine.number)
    with Session(app_engine) as s:
        assert s.get(Appeal, mine.id).status == AppealStatus.waiting_citizen


# ---------- foto və sənəd əlavələri ----------
JPEG, PDF = b"\xff\xd8\xff foto", b"%PDF-1.4 arayis"


def photo(file_id="P1", size=1000, caption=None):
    extra = {"photo": [{"file_id": file_id + "s", "file_size": 10}, {"file_id": file_id, "file_size": size}]}
    return extra | ({"caption": caption} if caption else {})


def document(file_id="D1", mime="application/pdf", name="arayış.pdf", size=1000):
    return {"document": {"file_id": file_id, "mime_type": mime, "file_name": name, "file_size": size}}


def attachments(bot) -> list:
    from app.db.tables import Attachment

    with bot.factory() as s:
        return list(s.scalars(select(Attachment).order_by(Attachment.id)))


def test_attachments_sent_while_writing_are_encrypted_and_bound_to_the_appeal(app_engine):
    bot = Bot(app_engine, ScriptedTurn(start_form_turn()))
    bot.telegram.files = {"P1": JPEG, "D1": PDF}
    assert "1/5" in texts(bot.say(extra=photo()))  # boşda: növbəti müraciətə əlavə olunacaq
    assert "📎 Əlavələr: 1" in texts(fill_new_profile(bot))
    assert "2/5" in texts(bot.say(extra=document()))  # təsdiq addımında da qəbul olunur
    bot.press(APPEAL_SEND)
    rows = attachments(bot)
    with bot.factory() as s:
        appeal = s.scalar(select(Appeal))
    assert [(r.kind, r.mime, r.appeal_id) for r in rows] == [("photo", "image/jpeg", appeal.id),
                                                              ("document", "application/pdf", appeal.id)]
    assert JPEG not in rows[0].content_enc and CRYPTO.decrypt_bytes(rows[0].content_enc) == JPEG
    assert CRYPTO.decrypt(rows[1].name_enc) == "arayış.pdf"


def test_attachment_rules(app_engine):
    bot = Bot(app_engine, ScriptedTurn())
    bot.telegram.files = {f"P{i}": JPEG for i in range(7)} | {"X": b"exe"}
    assert texts(bot.say(extra=document("X", mime="application/x-msdownload", name="a.exe"))) == \
        T["attachment_type"]
    assert "MB" in texts(bot.say(extra=document("D9", size=11 * 1024 * 1024)))
    assert texts(bot.say(extra=document("missing"))) == T["attachment_failed"]  # Telegram faylı vermədi
    for i in range(5):
        bot.say(extra=photo(f"P{i}"))
    assert texts(bot.say(extra=photo("P5"))) == T["attachment_limit"].format(limit=5)
    assert len(attachments(bot)) == 5


def test_attachment_with_caption_also_starts_the_dialogue(app_engine):
    turn = ScriptedTurn(start_form_turn())
    bot = Bot(app_engine, turn)
    bot.telegram.files = {"P1": JPEG}
    replies = bot.say(extra=photo(caption="Pensiyam 3 aydır köçmür, kağız əlavə edirəm"))
    assert turn.calls[0]["text"] == "Pensiyam 3 aydır köçmür, kağız əlavə edirəm"
    assert T["consent_prompt"] in texts(replies)


def test_attachments_are_not_logged_as_content(app_engine):
    bot = Bot(app_engine, ScriptedTurn())
    bot.telegram.files = {"D1": PDF}
    bot.say(extra=document())
    with bot.factory() as s:
        logs = [c for c in s.scalars(select(ChatLog.content))]
    assert "[əlavə: document]" in logs and not any("arayış" in c for c in logs)
