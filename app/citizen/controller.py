"""Vətəndaş botunun kontrolleri (spec 6-cı bölmə, plan 6.3).

Telegram update-i (runner API-yə JSON kimi ötürür) burada emal olunur:
- sual və müraciət niyyəti dialoq qrafına (8 agentin vətəndaş tərəfi) gedir;
- müraciət formu (razılıq, telefon, ad, FİN, ünvan, təsdiq) sabit kodla aparılır, LLM yoxdur;
- status, əməkdaşın sualına cavab və rəy birbaşa bazadan və AppealService ilə işlənir.

Qaydalar:
- Vətəndaşa gedən hər mesaj outbox-a eyni tranzaksiyada yazılır. Callback-lərə isə dərhal cavab verilir
  (Telegram düymənin "fırlanmasını" bir neçə saniyə gözləyir).
- LLM çağırışı DB tranzaksiyasından kənardadır.
- Formda yazılan şəxsi məlumatlar dərhal şifrələnib bazaya yazılır; chat_logs-a onların yerinə
  "[forma: ...]" qeydi düşür.
- Hər update bir dəfə emal olunur (runner təkrar göndərə bilər). Xəta olarsa, update unudulur ki,
  runner-in təkrar cəhdi onu yenidən emal etsin.
"""
import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.agents.citizen_graph import BTN_DISMISS, BTN_FAQ_HELPFUL, BTN_START_APPEAL, CitizenTurn
from app.citizen.session import CitizenSession, Limiter, SessionStore
from app.db.tables import Appeal, Attachment, ChatLog, Citizen, LiveChat, TelegramMessage
from app.livechat import LiveChats
from app.notifier.cards import FEEDBACK_NEG, FEEDBACK_POS, Rendered, keyboard
from app.notifier.notifier import CITIZEN_QUESTION, OutboxNotifier
from app.notifier.outbox import enqueue_rendered
from app.notifier.telegram_api import TelegramError
from app.privacy.crypto import Crypto, normalize_fin, normalize_phone
from app.privacy.display import mask_fin, mask_phone
from app.privacy.profile import pseudonymizer_for
from app.workflow.calendar import WorkCalendar
from app.workflow.errors import WorkflowError
from app.workflow.service import AppealService
from app.workflow.states import STATUS_LABELS, AppealStatus, BotKind
from app.yaml_config import AppConfig

log = logging.getLogger(__name__)

CONSENT_YES, CONSENT_NO = "consent:yes", "consent:no"
PROFILE_YES, PROFILE_UPDATE, MYDATA_UPDATE = "profile:yes", "profile:update", "mydata:update"
APPEAL_SEND, APPEAL_EDIT = "appeal:send", "appeal:edit"
PICK = "pick"

FORM_FIELDS = ("contact", "name", "fin", "address")
BUTTON_STEPS = ("consent", "confirm_profile", "confirm", "choose_appeal")
DIALOGUE_STEPS = ("intake", "intake_new")
BTN_OPERATOR = "operator"  # canlı çat (app/livechat.py), yalnız iş vaxtı təklif olunur
TURN_BUTTONS = {BTN_FAQ_HELPFUL: "👍 Kömək etdi", BTN_START_APPEAL: "📝 Müraciət etmək istəyirəm", BTN_DISMISS: "Xeyr",
                BTN_OPERATOR: "💬 Operatorla danış"}
CONTACT_KEYBOARD = {"keyboard": [[{"text": "📱 Nömrəmi paylaş", "request_contact": True}]],
                    "resize_keyboard": True, "one_time_keyboard": True}
REMOVE_KEYBOARD = {"remove_keyboard": True}
STATUS_LIMIT = 10

# Əlavələr: vətəndaş botu faylı yükləyir, şifrəli saxlanılır, müraciət yarananda ona bağlanır
MAX_ATTACHMENTS, MAX_ATTACHMENT_MB = 5, 10
ATTACHMENT_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf"}
NO_ATTACHMENT_STEPS = ("feedback_reason", "choose_appeal")

_FIN = re.compile(r"^[0-9A-Z]{7}$")
_NAME_WORD = re.compile(r"^[^\W\d_]+(?:[-'’][^\W\d_]+)*$")
ADDRESS_MIN, ADDRESS_MAX, NAME_PART_MAX = 10, 300, 64

Turn = Callable[..., CitizenTurn]


def utc_now() -> datetime:
    return datetime.now(UTC)


def _date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def parse_full_name(text: str) -> tuple[str, str, str] | None:
    """"Soyad Ad Ata adı..." → (soyad, ad, ata adı). Yalnız hərflər; ata adı bir neçə söz ola bilər."""
    words = text.split()
    if len(words) < 3 or not all(_NAME_WORD.match(w) for w in words):
        return None
    patronymic = " ".join(words[2:])
    if any(len(part) > NAME_PART_MAX for part in (words[0], words[1], patronymic)):
        return None
    return words[0], words[1], patronymic


@dataclass
class _Out:
    """Bir update-in yazma mərhələsi: baza sessiyası, servis və vətəndaşa gedən mesajlar."""
    s: Session
    chat_id: int
    now: datetime
    service: AppealService

    def say(self, text: str, markup: dict | None = None, *, intent: str | None = None, resolved: bool = False,
            appeal_id: int | None = None, log_as: str | None = None) -> None:
        """log_as: mətndə vətəndaşın profili varsa, chat_logs-a onun yerinə bu qeyd yazılır."""
        enqueue_rendered(self.s, bot=BotKind.citizen, chat_id=self.chat_id, rendered=Rendered(text, markup),
                         appeal_id=appeal_id, now=self.now)
        self.s.add(ChatLog(citizen_telegram_id=self.chat_id, role="bot", content=log_as or text, intent=intent,
                           resolved_by_ai=resolved))

    def heard(self, text: str, intent: str | None = None) -> None:
        self.s.add(ChatLog(citizen_telegram_id=self.chat_id, role="citizen", content=text, intent=intent))


class CitizenController:
    def __init__(self, sessions: sessionmaker[Session], *, store: SessionStore, limiter: Limiter, turn: Turn,
                 client, config: AppConfig, calendar: WorkCalendar, crypto: Crypto, org_name: str, hotline: str,
                 rate_limit: int, clock: Callable[[], datetime] = utc_now):
        self._sessions = sessions
        self.store, self.limiter, self.turn, self.client = store, limiter, turn, client
        self.config, self.calendar, self.crypto = config, calendar, crypto
        self.org_name, self.hotline, self.rate_limit = org_name, hotline, rate_limit
        self._clock = clock

    # ================= giriş =================
    def handle(self, update: dict) -> None:
        update_id = update.get("update_id")
        if "callback_query" in update:
            query = update["callback_query"]
            chat, user_id = (query.get("message") or {}).get("chat") or {}, query["from"]["id"]
            if not self._accept(update_id, chat, user_id):
                return
            self._answer_callback(query["id"])
            work = lambda: self._on_callback(user_id, query.get("data") or "")  # noqa: E731
        elif "message" in update:
            message = update["message"]
            user_id = (message.get("from") or {}).get("id")
            if user_id is None or not self._accept(update_id, message.get("chat") or {}, user_id):
                return
            work = lambda: self._on_message(user_id, message)  # noqa: E731
        else:
            return
        try:
            work()
        except Exception:
            if update_id is not None:
                self.limiter.forget(update_id)
            raise

    def _accept(self, update_id: int | None, chat: dict, user_id: int) -> bool:
        if chat.get("type", "private") != "private":
            return False  # bot yalnız şəxsi çatda işləyir
        if update_id is not None and not self.limiter.first_time(update_id):
            return False
        count = self.limiter.hit(user_id)
        if count > self.rate_limit:
            if count == self.rate_limit + 1:  # xəbərdarlıq bir dəfə
                with self._write(user_id) as out:
                    out.say(self._t("rate_limited"))
            return False
        return True

    def _answer_callback(self, query_id: str) -> None:
        try:
            self.client.answer_callback_query(query_id)
        except Exception as e:  # cavab verilməsə də düymənin işi görülməlidir
            log.warning("callback cavablandırılmadı (%s)", type(e).__name__)

    # ================= köməkçilər =================
    def _t(self, key: str, **values) -> str:
        return self.config.templates.render(key, **values)

    @contextmanager
    def _write(self, user_id: int) -> Iterator[_Out]:
        with self._sessions() as s, s.begin():
            notifier = OutboxNotifier(s, config=self.config, crypto=self.crypto, org_name=self.org_name,
                                      clock=self._clock)
            service = AppealService(s, config=self.config, calendar=self.calendar, crypto=self.crypto,
                                    notifier=notifier, clock=self._clock)
            yield _Out(s, user_id, self._clock(), service)

    @staticmethod
    def _citizen(s: Session, user_id: int) -> Citizen | None:
        return s.scalar(select(Citizen).where(Citizen.telegram_user_id == user_id))

    @staticmethod
    def _waiting(s: Session, citizen: Citizen | None) -> list[Appeal]:
        if citizen is None:
            return []
        return list(s.scalars(select(Appeal).where(Appeal.citizen_id == citizen.id,
                                                   Appeal.status == AppealStatus.waiting_citizen)
                              .order_by(Appeal.id)))

    def _consent_ok(self, citizen: Citizen | None) -> bool:
        return bool(citizen and citizen.consent_at and citizen.consent_version == self.config.consent.version)

    @staticmethod
    def _has(citizen: Citizen, field: str) -> bool:
        return bool({"contact": citizen.phone_enc, "fin": citizen.fin_enc, "address": citizen.address_enc,
                     "name": citizen.last_name and citizen.first_name and citizen.patronymic}[field])

    def _profile(self, citizen: Citizen) -> str:
        lines = [f"Ad: {citizen.last_name} {citizen.first_name} {citizen.patronymic}"]
        if citizen.phone_enc:
            lines.append(f"Telefon: {mask_phone(self.crypto.decrypt(citizen.phone_enc))}")
        if citizen.fin_enc:
            lines.append(f"FİN: {mask_fin(self.crypto.decrypt(citizen.fin_enc))}")
        if citizen.address_enc:
            lines.append(f"Ünvan: {self.crypto.decrypt(citizen.address_enc)}")
        return "\n".join(lines)

    def _complete(self, citizen: Citizen | None) -> bool:
        return self._consent_ok(citizen) and all(self._has(citizen, f) for f in FORM_FIELDS)

    # ================= mesajlar =================
    def _on_message(self, user_id: int, message: dict) -> None:
        text = (message.get("text") or "").strip()
        session = self.store.load(user_id)
        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else None

        if (message.get("photo") or message.get("document")) and self._in_live_chat(user_id):
            with self._write(user_id) as out:  # fayl operatora ötürülmür: əlavə kimi saxlamaq çaşdırardı
                out.heard("[canlı çatda fayl]")
                out.say(self._t("live_chat_text_only"))
            return
        if command in ("/operator", "/bitir") or (text and not command and self._in_live_chat(user_id)):
            with self._write(user_id) as out:
                self._live_chat_message(out, user_id, command, text)
            return
        if command == "/start":
            session.reset()
            with self._write(user_id) as out:
                out.heard("/start")
                out.say(self._t("greeting", org_name=self.org_name))
        elif command in ("/melumatlarim", "/məlumatlarım"):
            with self._write(user_id) as out:
                out.heard(command)
                self._show_my_data(out)
        elif message.get("photo") or message.get("document"):
            self._on_attachment(user_id, session, message)
            return  # sessiyanı özü saxlayır
        elif session.step in FORM_FIELDS or session.step in BUTTON_STEPS:
            with self._write(user_id) as out:
                self._form_input(out, session, user_id, text, message.get("contact"))
        elif session.step == "feedback_reason" and text:
            with self._write(user_id) as out:
                out.heard(text)
                self._negative_feedback(out, session, user_id, text)
        elif not text:
            with self._write(user_id) as out:
                out.heard("[mətn olmayan mesaj]")
                out.say(self._t("text_only"))
        else:
            reply_to = (message.get("reply_to_message") or {}).get("message_id")
            if reply_to is None or not self._bind_reply(user_id, reply_to, text):
                self._dialogue(user_id, session, text)
                return  # sessiyanı _dialogue özü saxlayır
        self.store.save(user_id, session)

    # ---------- canlı çat ----------
    def _live_chats(self, out: _Out) -> LiveChats:
        return LiveChats(out.s, now=out.now, templates=self.config.templates, hotline=self.hotline)

    def _in_live_chat(self, user_id: int) -> bool:
        with self._sessions() as s:
            return s.scalar(select(LiveChat.id).where(LiveChat.citizen_telegram_id == user_id,
                                                      LiveChat.status.in_(("waiting", "active")))) is not None

    def _live_chat_message(self, out: _Out, user_id: int, command: str | None, text: str) -> None:
        chats = self._live_chats(out)
        chat = chats.open_for_citizen(user_id, lock=True)
        if command == "/operator":
            out.heard(command)
            self._request_operator(out, user_id)
        elif command == "/bitir":
            out.heard(command)
            if chat is not None:
                chats.close(chat, by="citizen")
            else:
                out.say(self._t("live_chat_closed"))
        elif chat is not None:
            chats.from_citizen(chat, text)  # chat_logs-a özü yazır
        else:  # gözləmə müddəti indicə bitdi: vətəndaşa bildiriş gedib, mətn adi dialoqa düşmür
            out.heard(text)

    def _request_operator(self, out: _Out, user_id: int) -> None:
        if not self.calendar.is_work_time(out.now):
            out.say(self._t("live_chat_offline", hotline=f"\n{self.hotline}" if self.hotline else ""),
                    keyboard([(TURN_BUTTONS[BTN_START_APPEAL], BTN_START_APPEAL)]))
            return
        citizen = self._citizen(out.s, user_id)
        name = " ".join(n for n in (citizen.first_name, citizen.last_name) if n) if citizen else None
        self._live_chats(out).request(user_id, citizen_name=name or None)

    def _show_my_data(self, out: _Out) -> None:
        citizen = self._citizen(out.s, out.chat_id)
        if citizen is None or not citizen.last_name:
            out.say(self._t("no_profile"))
        else:
            out.say(self._t("my_data", profile=self._profile(citizen)), keyboard([("✏️ Yenilə", MYDATA_UPDATE)]),
                    log_as="[profil göstərildi]")

    # ---------- dialoq qrafı ----------
    def _dialogue(self, user_id: int, session: CitizenSession, text: str, *, step: str | None = None,
                  log_text: bool = True) -> None:
        with self._sessions() as s:
            citizen = self._citizen(s, user_id)
            pseudo = pseudonymizer_for(citizen, self.crypto)
            has_open_question = bool(self._waiting(s, citizen))
        step = step or (session.step if session.step in DIALOGUE_STEPS else "idle")
        try:
            turn = self.turn(citizen_key=user_id, text=text, step=step, has_open_question=has_open_question,
                             pseudo=pseudo)
        except Exception:  # qraf (checkpoint bazası və s.) düşsə də vətəndaş cavabsız qalmır
            log.exception("vətəndaş dialoqu alınmadı")
            turn = CitizenTurn(intent="question", reply=self._t("llm_unavailable"), next_step="idle",
                               buttons=[BTN_START_APPEAL])

        with self._write(user_id) as out:
            if log_text:
                out.heard(text, turn.intent)
            if turn.intent == "question":
                session.last_question = text
            session.step = "intake" if turn.next_step == "intake" else "idle"
            if turn.reply:
                names, reply = list(turn.buttons), turn.reply
                if turn.intent == "human" and self.calendar.is_work_time(out.now):
                    names.insert(0, BTN_OPERATOR)  # mətn də canlı operatoru təklif etsin (real sınaqdan)
                    reply = self._t("human_handoff_live")
                buttons = keyboard([(TURN_BUTTONS[b], b) for b in names]) if names else None
                out.say(reply, buttons, intent=turn.intent, resolved=turn.resolved_by_ai)
            for effect in turn.effects:
                self._effect(out, session, user_id, effect)
        self.store.save(user_id, session)

    def _effect(self, out: _Out, session: CitizenSession, user_id: int, effect: dict) -> None:
        kind = effect.get("type")
        if kind == "start_form":
            session.draft = {k: effect[k] for k in ("subject", "summary", "description")}
            session.redo = session.profile_only = session.profile_ok = False
            self._advance(out, session, user_id)
        elif kind == "show_status":
            self._status(out, user_id)
        elif kind == "forward_clarification":
            self._clarification(out, session, user_id, effect["text"])

    def _status(self, out: _Out, user_id: int) -> None:
        citizen = self._citizen(out.s, user_id)
        appeals = [] if citizen is None else out.s.scalars(
            select(Appeal).where(Appeal.citizen_id == citizen.id).order_by(Appeal.id.desc()).limit(STATUS_LIMIT)).all()
        if not appeals:
            out.say(self._t("no_appeals"))
            return
        out.say("\n".join(self._t("status_line", number=a.number, subject=a.subject, status=STATUS_LABELS[a.status],
                                  due_date=_date(a.due_date)) for a in appeals))

    # ---------- əməkdaşın sualına cavab (spec 6-C) ----------
    def _bind_reply(self, user_id: int, reply_to: int, text: str) -> bool:
        """1-ci qayda: vətəndaş sual mesajına reply edibsə, cavab o müraciətə bağlanır (qraf işləmir)."""
        with self._write(user_id) as out:
            row = out.s.scalar(select(TelegramMessage).where(
                TelegramMessage.bot == BotKind.citizen, TelegramMessage.chat_id == user_id,
                TelegramMessage.message_id == reply_to, TelegramMessage.purpose == CITIZEN_QUESTION))
            if row is None or row.appeal_id is None:
                return False
            out.heard(text, "clarification_reply")
            self._reply_to(out, user_id, out.s.get(Appeal, row.appeal_id), text)
            return True

    def _reply_to(self, out: _Out, user_id: int, appeal: Appeal, text: str) -> None:
        citizen = self._citizen(out.s, user_id)
        if citizen is None or appeal.citizen_id != citizen.id or appeal.status != AppealStatus.waiting_citizen:
            out.say(self._t("question_closed", number=appeal.number))
            return
        out.service.citizen_reply(citizen.id, appeal.id, text)
        out.say(self._t("reply_forwarded", number=appeal.number), appeal_id=appeal.id)

    def _clarification(self, out: _Out, session: CitizenSession, user_id: int, text: str) -> None:
        """2-ci və 3-cü qaydalar: tək gözləyən müraciət → ona; bir neçə → vətəndaş seçir."""
        waiting = self._waiting(out.s, self._citizen(out.s, user_id))
        if len(waiting) == 1:
            self._reply_to(out, user_id, waiting[0], text)
        elif waiting:
            session.step, session.pending_reply = "choose_appeal", text
            out.say(self._t("choose_appeal"), keyboard(*[[(a.number, f"{PICK}:{a.id}")] for a in waiting]))
        else:
            out.say(self._t("dismissed"))

    # ---------- müraciət formu ----------
    def _advance(self, out: _Out, session: CitizenSession, user_id: int, *, after: str | None = None,
                 markup: dict | None = None) -> None:
        """Növbəti addım: razılıq → çatışmayan (və ya yenilənən) sahələr → profilin/müraciətin təsdiqi."""
        citizen = self._citizen(out.s, user_id)
        if not self._consent_ok(citizen):
            session.step = "consent"
            consent = self.config.consent.text.replace("{org_name}", self.org_name).replace("**", "")
            out.say(f"{self._t('consent_prompt')}\n\n{consent}",
                    keyboard([("✅ Razıyam", CONSENT_YES), ("Razı deyiləm", CONSENT_NO)]))
            return
        start = FORM_FIELDS.index(after) + 1 if after else 0
        for field in FORM_FIELDS[start:]:
            if session.redo or not self._has(citizen, field):
                session.step = field
                ask = {"contact": "ask_phone", "name": "ask_name", "fin": "ask_fin", "address": "ask_address"}[field]
                out.say(self._t(ask), CONTACT_KEYBOARD if field == "contact" else markup)
                return
        session.redo = False
        if session.profile_only:
            session.reset()
            out.say(self._t("my_data", profile=self._profile(citizen)), markup, log_as="[profil yeniləndi]")
        elif not session.profile_ok:
            session.step = "confirm_profile"
            out.say(self._t("confirm_profile", profile=self._profile(citizen)),
                    keyboard([("✅ Bəli", PROFILE_YES), ("✏️ Yenilə", PROFILE_UPDATE)]), log_as="[profilin təsdiqi]")
        else:
            session.step = "confirm"
            files = len(session.extra.get("attachments", []))
            out.say(self._t("confirm_appeal", subject=session.draft["subject"], summary=session.draft["summary"],
                            profile=self._profile(citizen)) + (f"\n📎 Əlavələr: {files}" if files else ""),
                    keyboard([("✅ Göndər", APPEAL_SEND), ("✏️ Düzəlt", APPEAL_EDIT)]),
                    log_as=f"[müraciətin təsdiqi: {session.draft['subject']}]")

    def _form_input(self, out: _Out, session: CitizenSession, user_id: int, text: str, contact: dict | None) -> None:
        step = session.step
        out.heard(f"[forma: {step}]")
        if step in BUTTON_STEPS:
            out.say(self._t("use_buttons"))
            return
        citizen = self._citizen(out.s, user_id)
        if step == "contact":
            if not contact:
                out.say(self._t("ask_phone"), CONTACT_KEYBOARD)
                return
            if contact.get("user_id") != user_id:  # yalnız öz nömrəsi (Telegram tərəfindən təsdiqlənmiş)
                out.say(self._t("contact_not_own"), CONTACT_KEYBOARD)
                return
            phone = normalize_phone(contact.get("phone_number") or "")
            citizen.phone_enc, citizen.phone_hash = self.crypto.encrypt(phone), self.crypto.blind_index("phone", phone)
        elif step == "name":
            parts = parse_full_name(text)
            if parts is None:
                out.say(self._t("invalid_name"))
                return
            citizen.last_name, citizen.first_name, citizen.patronymic = parts
        elif step == "fin":
            fin = normalize_fin("".join(text.split()))
            if not _FIN.match(fin):
                out.say(self._t("invalid_fin"))
                return
            citizen.fin_enc, citizen.fin_hash = self.crypto.encrypt(fin), self.crypto.blind_index("fin", fin)
        elif step == "address":
            if not ADDRESS_MIN <= len(text) <= ADDRESS_MAX:
                out.say(self._t("invalid_address"))
                return
            citizen.address_enc = self.crypto.encrypt(text)
        session.profile_ok = True
        self._advance(out, session, user_id, after=step, markup=REMOVE_KEYBOARD if step == "contact" else None)

    # ================= düymələr =================
    def _on_attachment(self, user_id: int, session: CitizenSession, message: dict) -> None:
        if message.get("photo"):
            biggest = message["photo"][-1]
            kind, file_id, mime, size = "photo", biggest["file_id"], "image/jpeg", biggest.get("file_size", 0)
            name = None
        else:
            doc = message["document"]
            kind, file_id, size = "document", doc["file_id"], doc.get("file_size", 0)
            mime, name = (doc.get("mime_type") or "").lower(), doc.get("file_name")
        ids: list[int] = session.extra.get("attachments", [])
        limit_bytes = MAX_ATTACHMENT_MB * 1024 * 1024

        error, content = None, None
        if session.step in NO_ATTACHMENT_STEPS:
            error = self._t("text_only")
        elif len(ids) >= MAX_ATTACHMENTS:
            error = self._t("attachment_limit", limit=MAX_ATTACHMENTS)
        elif mime not in ATTACHMENT_TYPES:
            error = self._t("attachment_type")
        elif size > limit_bytes:
            error = self._t("attachment_too_big", mb=MAX_ATTACHMENT_MB)
        else:
            try:  # şəbəkə: DB tranzaksiyasından kənarda
                content = self.client.download_file(file_id)
            except TelegramError as e:
                log.warning("əlavə yüklənmədi (%s)", type(e).__name__)
                error = self._t("attachment_failed")
            else:
                if len(content) > limit_bytes:
                    error = self._t("attachment_too_big", mb=MAX_ATTACHMENT_MB)

        with self._write(user_id) as out:
            out.heard(f"[əlavə: {kind}]")
            if error:
                out.say(error)
            else:
                name = name or f"{kind}_{len(ids) + 1}{ATTACHMENT_TYPES[mime]}"
                row = Attachment(owner_telegram_id=user_id, kind=kind, mime=mime, size=len(content),
                                 name_enc=self.crypto.encrypt(name[:255]),
                                 content_enc=self.crypto.encrypt_bytes(content))
                out.s.add(row)
                out.s.flush()
                session.extra["attachments"] = [*ids, row.id]
                out.say(self._t("attachment_saved", count=len(ids) + 1, limit=MAX_ATTACHMENTS))
        caption = (message.get("caption") or "").strip()
        if not error and caption and session.step in ("idle", *DIALOGUE_STEPS):
            self._dialogue(user_id, session, caption)  # sessiyanı özü saxlayır
            return
        self.store.save(user_id, session)

    def _on_callback(self, user_id: int, data: str) -> None:
        session = self.store.load(user_id)
        if data == BTN_OPERATOR:
            with self._write(user_id) as out:
                out.heard("💬 Operatorla danış")
                self._request_operator(out, user_id)
            return
        if data == BTN_START_APPEAL:
            if session.last_question:
                self._dialogue(user_id, session, session.last_question, step="intake_new", log_text=False)
                return
            session.step = "intake_new"
            with self._write(user_id) as out:
                out.say(self._t("describe_problem"))
        else:
            with self._write(user_id) as out:
                self._button(out, session, user_id, data)
        self.store.save(user_id, session)

    def _button(self, out: _Out, session: CitizenSession, user_id: int, data: str) -> None:
        code, _, arg = data.partition(":")
        step = session.step
        if data in (CONSENT_YES, CONSENT_NO) and step == "consent":
            out.heard(data)
            if data == CONSENT_NO:
                session.reset()
                out.say(self._t("consent_declined"))
                return
            citizen = self._citizen(out.s, user_id)
            if citizen is None:
                citizen = Citizen(telegram_user_id=user_id)
                out.s.add(citizen)
            citizen.consent_version, citizen.consent_at = self.config.consent.version, out.now
            out.s.flush()
            self._advance(out, session, user_id)
        elif data in (PROFILE_YES, PROFILE_UPDATE) and step == "confirm_profile":
            session.profile_ok, session.redo = True, data == PROFILE_UPDATE
            self._advance(out, session, user_id)
        elif data == MYDATA_UPDATE and self._complete(self._citizen(out.s, user_id)):
            session.reset()
            session.profile_only = session.redo = session.profile_ok = True
            self._advance(out, session, user_id)
        elif data == APPEAL_SEND and step == "confirm":
            self._register(out, session, user_id)
        elif data == APPEAL_EDIT and step == "confirm":
            session.reset()
            out.say(self._t("appeal_cancelled"))
        elif data == BTN_FAQ_HELPFUL:
            out.heard("👍 FAQ cavabı kömək etdi", "question")
            out.say(self._t("feedback_thanks"))
        elif data == BTN_DISMISS:
            session.step = "idle"
            out.say(self._t("dismissed"))
        elif code == PICK and step == "choose_appeal" and arg.isdigit():
            appeal = out.s.get(Appeal, int(arg))
            text = session.pending_reply or ""
            session.reset()
            if appeal is not None and text:
                out.heard(text, "clarification_reply")
                self._reply_to(out, user_id, appeal, text)
        elif code in (FEEDBACK_POS, FEEDBACK_NEG) and arg.isdigit():
            self._feedback_button(out, session, user_id, code, int(arg))

    def _register(self, out: _Out, session: CitizenSession, user_id: int) -> None:
        citizen = self._citizen(out.s, user_id)
        draft = session.draft or {}
        try:
            with out.s.begin_nested():
                appeal = out.service.create_appeal(citizen.id, subject=draft["subject"], summary=draft["summary"],
                                                   description=draft["description"])
                if ids := session.extra.get("attachments"):
                    out.s.execute(update(Attachment).where(
                        Attachment.id.in_(ids), Attachment.owner_telegram_id == user_id,
                        Attachment.appeal_id.is_(None)).values(appeal_id=appeal.id))
        except (WorkflowError, KeyError) as e:
            log.warning("müraciət qeydə alınmadı (%s)", type(e).__name__)
            out.say(self._t("registration_failed"))
            return
        session.reset()
        out.say(self._t("appeal_registered", number=appeal.number, due_date=_date(appeal.due_date)),
                appeal_id=appeal.id)

    def _feedback_button(self, out: _Out, session: CitizenSession, user_id: int, code: str, appeal_id: int) -> None:
        citizen = self._citizen(out.s, user_id)
        appeal = out.s.get(Appeal, appeal_id)
        if citizen is None or appeal is None or appeal.citizen_id != citizen.id \
                or appeal.status != AppealStatus.answered:
            return  # köhnə və ya başqasının düyməsi: callback onsuz da cavablandırılıb
        if code == FEEDBACK_POS:
            out.heard("👍 Qane oldum")
            out.service.citizen_feedback(citizen.id, appeal_id, satisfied=True)
            out.say(self._t("feedback_thanks"), appeal_id=appeal_id)
        else:
            out.heard("👎 Qane olmadım")
            session.step, session.feedback_appeal_id = "feedback_reason", appeal_id
            out.say(self._t("feedback_reason_prompt"), appeal_id=appeal_id)

    def _negative_feedback(self, out: _Out, session: CitizenSession, user_id: int, reason: str) -> None:
        appeal_id = session.feedback_appeal_id
        session.reset()
        citizen = self._citizen(out.s, user_id)
        try:
            with out.s.begin_nested():
                out.service.citizen_feedback(citizen.id, appeal_id, satisfied=False, reason=reason)
        except WorkflowError as e:
            log.warning("mənfi rəy qəbul olunmadı (%s)", type(e).__name__)
            out.say(self._t("dismissed"))
