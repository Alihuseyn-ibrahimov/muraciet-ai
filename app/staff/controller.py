"""Əməkdaş botunun kontrolleri (spec 7-ci bölmə, plan 7.2).

- Yalnız aktiv əməkdaş işləyə bilər. Qalanlar rədd edilir və cəhd loga yazılır. İstisna: dəvət linki
  (`/start inv_<token>`), token bazada yalnız heş kimi saxlanılır, 24 saat və bir dəfə etibarlıdır.
- Kart düymələri `kod:müraciət:versiya[:əlavə]` formatındadır (app/notifier/cards.py). Müraciət bu
  arada dəyişibsə, servis StaleAppeal verir və düymə "köhnəlib" cavabı alır.
- Mətn tələb edən əməliyyatlarda (cavab, sual, şərh, orqanın adı...) icazə və versiya düymə basılan
  anda yoxlanılır ki, əməkdaş mətni yazıb sonra rədd almasın. Yarımçıq əməliyyat Redis-də 30 dəqiqə qalır.
- Cavab: nəticə → mətn (və ya AI layihəsi) → Keyfiyyət agenti → önizləmə → göndər və ya təsdiqə.
  Keyfiyyət yoxlaması (LLM) DB tranzaksiyasından kənardadır.
- FİN mesajla yox, callback xəbərdarlığı (alert) ilə göstərilir: çat tarixçəsində və outbox-da qalmır.
"""
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.schemas import QaReport
from app.citizen.session import Limiter, SessionStore
from app.db.tables import Appeal, AppealMessage, Citizen, Department, KbEntry, Staff, StaffInvite, TelegramMessage
from app.notifier import cards
from app.notifier.cards import (
    ANSWER, APPROVE, ASK, ASSIGN, ATTACHMENTS, EXTEND, FIN, KB_APPROVE, KB_PROPOSAL, KB_REJECT, LIVE_CHAT_ACCEPT, NOT_REVIEW, OTHER,
    REDIRECT, RETURN, SEND_BACK, USE_DRAFT, Rendered, callback, keyboard, parse_callback,
)
from app.livechat import LiveChats
from app.notifier.notifier import OutboxNotifier
from app.notifier.outbox import SECRET_PURPOSE, enqueue_document, enqueue_edit, enqueue_rendered
from app.privacy.crypto import Crypto
from app.privacy.profile import pseudonymizer_for
from app.staff.invites import INVITE_HOURS, create_invite, invite_link, token_hash
from app.staff.session import StaffSession
from app.workflow.approval import needs_approval
from app.workflow.authz import Actor, authorize, require_admin
from app.workflow.calendar import WorkCalendar
from app.workflow.errors import Forbidden, InvalidTransition, NotFound, StaleAppeal, ValidationFailed, WorkflowError
from app.workflow.machine import Action, next_status
from app.workflow.service import AppealService, has_placeholder
from app.workflow.states import (
    OUTCOME_LABELS, STAFF_STATUS_LABELS, AppealStatus, BotKind, MessageKind, Outcome, Priority, StaffRole,
)
from app.workflow.suggest import ACTIVE_WORK, open_appeal_count
from app.yaml_config import AppConfig

log = logging.getLogger(__name__)

# yalnız əməkdaş botunun düymələri (kartlarınkı app/notifier/cards.py-dadır)
OUTCOME, SEND, REWRITE = "oc", "sd", "rw"
NOT_REVIEW_REASON, EXTEND_DAYS, SKIP_LETTER, OPEN = "nrr", "exd", "skl", "op"
INVITE_ROLE, INVITE_DEPT, DEACTIVATE = "iv", "ivd", "dx"

MAX_TEXT = 4000
EXTEND_OPTIONS = (5, 10, 15, 30)
OPEN_STATUSES = (AppealStatus.new, AppealStatus.assigned, AppealStatus.waiting_citizen, AppealStatus.pending_approval)
ROLE_LABELS = {StaffRole.coordinator: "Koordinator", StaffRole.staff: "İcraçı", StaffRole.head: "Rəhbər"}
# Qanunun 8.1-ci maddəsi: baxılmamış saxlama əsasları
NOT_REVIEW_REASONS = {
    "1": ("8.1.1 Başqa qayda", "m. 8.1.1: bu məsələ üzrə müraciət üçün qanunla başqa qayda nəzərdə tutulub"),
    "2": ("8.1.2 Tələblərə cavab vermir", "m. 8.1.2: müraciət Qanunun 6.4–6.9-cu maddələrinin tələblərinə cavab vermir"),
    "3": ("8.1.3 Məhkəmə qərarı var", "m. 8.1.3: məsələ üzrə məhkəmənin qanuni qüvvəyə minmiş qərarı var"),
    "4": ("8.1.4 Təkrar müraciət", "m. 8.1.4: bir il ərzində 3 dəfə əsaslandırılmış cavab verilib, yeni məlumat yoxdur"),
    "5": ("8.1.5 Məlumat kifayət deyil", "m. 8.1.5: məlumat həll üçün kifayət etmir və ya aidiyyəti üzrə göndərmək "
                                         "mümkün deyil"),
    "6": ("8.1.6 Vətəndaş dayandırdı", "m. 8.1.6: vətəndaş müraciətə baxılmanın dayandırılmasını xahiş edib"),
}
PROMPTS = {
    "answer_text": "✍️ Cavabın mətnini yazın (ən çoxu 4000 simvol). Ləğv: /legv",
    "ask": "❓ Vətəndaşa sualınızı yazın. Ləğv: /legv",
    "return": "↩️ Koordinatora qaytarmağın səbəbini yazın. Ləğv: /legv",
    "send_back": "↩️ İcraçıya şərhinizi yazın (nəyi düzəltməlidir). Ləğv: /legv",
    "redirect_target": "↪️ Müraciətin göndəriləcəyi orqanın adını yazın. Ləğv: /legv",
    "not_review_text": "⏸ Səbəbi qısa izah edin, vətəndaşa göndəriləcək. Ləğv: /legv",
    "extend_reason": "⏳ Uzatmanın səbəbini yazın, vətəndaşa göndəriləcək. Ləğv: /legv",
}
REFUSED = "Bu bot yalnız qurum əməkdaşları üçündür."
STALE = "⏳ Bu əməliyyat artıq köhnəlib: müraciətin vəziyyəti dəyişib."
FORBIDDEN = "⛔ Bu əməliyyata icazəniz yoxdur."
HELP = {
    StaffRole.coordinator: "Əmrlər: /panel, /tap MR-2026-000123, /islerim, /legv, /bitir (canlı çat)",
    StaffRole.staff: "Əmrlər: /islerim, /tap MR-2026-000123, /legv",
    StaffRole.head: "Əmrlər: /panel, /tap MR-2026-000123",
}

Qa = Callable[..., QaReport]


def utc_now() -> datetime:
    return datetime.now(UTC)


def error_text(e: WorkflowError) -> str:
    if isinstance(e, StaleAppeal | InvalidTransition):
        return STALE
    if isinstance(e, Forbidden):
        return FORBIDDEN
    if isinstance(e, NotFound):
        return "Tapılmadı."
    return str(e)


def actor_of(staff: Staff) -> Actor:
    return Actor.staff(staff.id, staff.role, is_admin=staff.is_admin, is_active=staff.is_active)


@dataclass
class _Out:
    s: Session
    chat_id: int
    now: datetime
    service: AppealService
    notifier: OutboxNotifier

    def say(self, text: str, markup: dict | None = None, *, appeal_id: int | None = None,
            chat_id: int | None = None, purpose: str | None = None) -> None:
        enqueue_rendered(self.s, bot=BotKind.staff, chat_id=chat_id or self.chat_id, rendered=Rendered(text, markup),
                         appeal_id=appeal_id, purpose=purpose, now=self.now)


class StaffController:
    def __init__(self, sessions: sessionmaker[Session], *, store: SessionStore, limiter: Limiter, client,
                 config: AppConfig, calendar: WorkCalendar, crypto: Crypto, org_name: str, qa: Qa,
                 bot_username: str, clock: Callable[[], datetime] = utc_now,
                 kb_add: Callable[[int, str, str], None] | None = None, hotline: str = ""):
        self._sessions = sessions
        self.hotline = hotline
        self.kb_add = kb_add  # təsdiqlənmiş təklifi bilik bazasına yazır (id, sual, cavab)
        self.store, self.limiter, self.client = store, limiter, client
        self.config, self.calendar, self.crypto, self.org_name = config, calendar, crypto, org_name
        self.qa, self.bot_username, self._clock = qa, bot_username, clock

    # ================= giriş =================
    def handle(self, update: dict) -> None:
        update_id = update.get("update_id")
        if "callback_query" in update:
            query = update["callback_query"]
            chat = (query.get("message") or {}).get("chat") or {}
            if not self._accept(update_id, chat):
                return
            alert = None
            try:
                alert = self._guarded(update_id, lambda: self._on_callback(query["from"]["id"], query.get("data") or ""))
            finally:
                self._answer_callback(query["id"], alert)
        elif "message" in update:
            message = update["message"]
            if not self._accept(update_id, message.get("chat") or {}):
                return
            self._guarded(update_id, lambda: self._on_message(message))

    def _accept(self, update_id: int | None, chat: dict) -> bool:
        if chat.get("type", "private") != "private":
            return False
        return update_id is None or self.limiter.first_time(update_id)

    def _guarded(self, update_id: int | None, work: Callable):
        try:
            return work()
        except Exception:
            if update_id is not None:
                self.limiter.forget(update_id)  # runner təkrar göndərəndə yenidən emal olunsun
            raise

    def _answer_callback(self, query_id: str, alert: str | None) -> None:
        try:
            self.client.answer_callback_query(query_id, alert, show_alert=bool(alert))
        except Exception as e:
            log.warning("callback cavablandırılmadı (%s)", type(e).__name__)

    # ================= köməkçilər =================
    @contextmanager
    def _write(self, chat_id: int) -> Iterator[_Out]:
        with self._sessions() as s, s.begin():
            notifier = OutboxNotifier(s, config=self.config, crypto=self.crypto, org_name=self.org_name,
                                      clock=self._clock)
            service = AppealService(s, config=self.config, calendar=self.calendar, crypto=self.crypto,
                                    notifier=notifier, clock=self._clock)
            yield _Out(s, chat_id, self._clock(), service, notifier)

    @staticmethod
    def _staff(s: Session, user_id: int) -> Staff | None:
        staff = s.scalar(select(Staff).where(Staff.telegram_user_id == user_id))
        return staff if staff is not None and staff.is_active else None

    @staticmethod
    def _check(s: Session, staff: Staff, appeal_id: int, version: int | None, action: Action) -> Appeal:
        """Mətn yazdırmazdan əvvəl: müraciət var, icazə var, keçid mümkündür, düymə köhnəlməyib."""
        appeal = s.get(Appeal, appeal_id)
        if appeal is None:
            raise NotFound("Müraciət tapılmadı")
        authorize(actor_of(staff), action, appeal)
        if action not in (Action.reveal_fin, Action.view_attachments):
            next_status(appeal.status, action)
        if version is not None and appeal.version != version:
            raise StaleAppeal("köhnəlib", {})
        return appeal

    def _pending(self, user_id: int, session: StaffSession, action: str, appeal: Appeal, out: _Out, **extra) -> None:
        session.reset()
        session.action, session.appeal_id, session.version = action, appeal.id, appeal.version
        session.extra.update(extra)
        out.say(f"{appeal.number}: {PROMPTS[action]}", appeal_id=appeal.id)

    # ================= mesajlar =================
    def _on_message(self, message: dict) -> None:
        sender = message.get("from") or {}
        user_id, text = sender.get("id"), (message.get("text") or "").strip()
        command, _, arg = text.partition(" ")
        command = command.split("@")[0].lower() if command.startswith("/") else None
        session = self.store.load(user_id)

        with self._write(user_id) as out:
            if command == "/start" and arg.strip().startswith("inv_"):
                self._register(out, sender, arg.strip()[4:])
                return
            staff = self._staff(out.s, user_id)
            if staff is None:
                log.warning("əməkdaş botu: qeydiyyatsız və ya deaktiv şəxs yazdı (telegram_id=%s)", user_id)
                out.say(REFUSED)
                return
            if command == "/legv":
                session.reset()
                out.say("Ləğv edildi.")
            elif command in ("/start", "/help"):
                out.say(f"Salam, {staff.full_name}! {HELP[staff.role]}")
            elif command == "/panel":
                self._panel(out, staff)
            elif command == "/islerim":
                self._my_work(out, staff)
            elif command == "/tap":
                self._find(out, staff, arg.strip().upper())
            elif command == "/devet":
                self._invite_menu(out, staff)
            elif command == "/emekdaslar":
                self._staff_list(out, staff)
            elif command == "/bitir":
                chats = self._live_chats(out)
                chat = chats.active_for_staff(staff.id)
                if chat is None:
                    out.say("Aktiv canlı çatınız yoxdur.")
                else:
                    chats.close(chat, by="staff")
            elif session.action and text:
                if len(text) > MAX_TEXT:
                    out.say(f"Mətn {MAX_TEXT} simvoldan uzun ola bilməz. Qısaldın və yenidən göndərin.")
                elif session.action == "answer_text":
                    pass  # keyfiyyət yoxlaması (LLM) tranzaksiyadan kənarda, aşağıda
                else:
                    self._finish_text(out, staff, session, text)
            elif text and not command and (chat := self._live_chats(out).active_for_staff(staff.id)):
                self._live_chats(out).from_staff(chat, staff, text)  # cari canlı çat: mətn vətəndaşa gedir
            else:
                out.say(HELP[staff.role])
        if session.action == "answer_text" and text and not command and len(text) <= MAX_TEXT:
            self._preview(user_id, session, text)
        self.store.save(user_id, session)

    def _finish_text(self, out: _Out, staff: Staff, session: StaffSession, text: str) -> None:
        actor, appeal_id, version, action = actor_of(staff), session.appeal_id, session.version, session.action
        if action == "redirect_target":
            session.action, session.extra["target"] = "redirect_letter", text
            out.say("Məktubun nömrəsini və tarixini yazın (m. 14.0.6), yoxdursa ⏭ Keç basın.",
                    keyboard([("⏭ Keç", callback(SKIP_LETTER, appeal_id, version))]), appeal_id=appeal_id)
            return
        try:
            with out.s.begin_nested():
                if action == "ask":
                    appeal = out.service.ask_citizen(actor, appeal_id, text, expected_version=version)
                    done = f"❓ {appeal.number}: sual vətəndaşa göndərildi."
                elif action == "return":
                    appeal = out.service.return_to_coordinator(actor, appeal_id, text, expected_version=version)
                    done = f"↩️ {appeal.number}: koordinatora qaytarıldı."
                elif action == "send_back":
                    appeal = out.service.send_back(actor, appeal_id, text, expected_version=version)
                    done = f"↩️ {appeal.number}: cavab icraçıya qaytarıldı."
                elif action == "redirect_letter":
                    appeal = self._redirect(out, actor, session, text)
                    done = f"↪️ {appeal.number}: aidiyyəti üzrə göndərildi, vətəndaşa bildiriş gedir."
                elif action == "not_review_text":
                    reason = f"{NOT_REVIEW_REASONS[session.extra['reason']][1]}. {text}"
                    appeal = out.service.not_review(actor, appeal_id, reason=reason, expected_version=version)
                    done = f"⏸ {appeal.number}: baxılmamış saxlanıldı, vətəndaşa bildiriş gedir."
                elif action == "extend_reason":
                    appeal = out.service.extend(actor, appeal_id, workdays=int(session.extra["workdays"]),
                                                reason=text, expected_version=version)
                    done = f"⏳ {appeal.number}: son tarix {appeal.due_date:%d.%m.%Y}, vətəndaşa bildiriş gedir."
                else:
                    return
        except WorkflowError as e:
            out.say(error_text(e))
        else:
            out.say(done, appeal_id=appeal_id)
        session.reset()

    def _redirect(self, out: _Out, actor: Actor, session: StaffSession, letter_ref: str | None) -> Appeal:
        return out.service.redirect(actor, session.appeal_id, target=session.extra["target"], letter_ref=letter_ref,
                                    expected_version=session.version)

    # ---------- cavab: keyfiyyət yoxlaması və önizləmə ----------
    def _preview(self, user_id: int, session: StaffSession, text: str) -> None:
        with self._sessions() as s:
            appeal = s.get(Appeal, session.appeal_id)
            citizen = s.get(Citizen, appeal.citizen_id)
            summary, pseudo = appeal.summary, pseudonymizer_for(citizen, self.crypto)
        outcome = Outcome(session.outcome)
        try:
            report = self.qa(appeal_summary=summary, answer=text, outcome=outcome, pseudo=pseudo)
        except Exception as e:  # yoxlama düşsə də əməkdaş işləyə bilər, cavab isə təsdiqə düşür
            log.warning("keyfiyyət yoxlaması alınmadı (%s)", type(e).__name__)
            report = QaReport(complete=False)
        critical = True if report.has_critical else (False if report.complete else None)
        session.action, session.text, session.qa_critical = "preview", text, critical

        with self._write(user_id) as out:
            appeal = out.s.get(Appeal, session.appeal_id)
            staff = self._staff(out.s, user_id)
            decision = needs_approval(appeal, outcome=outcome, qa_critical=critical, actor=actor_of(staff),
                                      rules=self.config.approval)
            shown = "\n\n".join(part.text for part in cards.citizen_answer(
                appeal, answer=text, outcome=outcome, templates=self.config.templates, org_name=self.org_name))
            icons = {"critical": "🔴", "warning": "⚠️", "info": "ℹ️"}
            notes = [f"{icons[i.severity]} {i.message}" for i in report.issues]
            if not report.complete:
                notes.append("⚠️ AI keyfiyyət yoxlaması aparılmadı")
            if decision.required:
                notes.append("🟡 Təsdiq lazımdır: " + "; ".join(decision.reasons))
            unfinished = has_placeholder(text)
            if unfinished:
                notes.insert(0, "🔴 Mətndə doldurulmamış yerlər var ([...]): tamamlayıb yenidən yazın. "
                                "Belə cavab vətəndaşa göndərilmir")
            body = "\n".join(notes) if notes else "✅ Keyfiyyət yoxlaması: irad yoxdur"
            send_label = "📤 Təsdiqə göndər" if decision.required else "📤 Göndər"
            row = [] if unfinished else [(send_label, callback(SEND, appeal.id, session.version))]
            out.say(f"👁 Önizləmə — vətəndaş belə görəcək:\n\n{shown}\n\n{body}",
                    keyboard(row + [("✏️ Yenidən yaz", callback(REWRITE, appeal.id, session.version))]),
                    appeal_id=appeal.id)

    # ================= düymələr =================
    def _on_callback(self, user_id: int, data: str) -> str | None:
        session = self.store.load(user_id)
        code = data.partition(":")[0]
        preview_text = None
        with self._write(user_id) as out:
            staff = self._staff(out.s, user_id)
            if staff is None:
                log.warning("əməkdaş botu: qeydiyyatsız və ya deaktiv şəxs düymə basdı (telegram_id=%s)", user_id)
                return REFUSED
            try:
                with out.s.begin_nested():
                    if code in (INVITE_ROLE, INVITE_DEPT, DEACTIVATE):
                        alert = self._admin_button(out, staff, code, data.partition(":")[2])
                    elif code == LIVE_CHAT_ACCEPT:
                        if staff.role != StaffRole.coordinator:
                            raise Forbidden("canlı çatı koordinator qəbul edir")
                        alert = self._live_chats(out).accept(parse_callback(data).appeal_id, staff)
                    elif code in (KB_APPROVE, KB_REJECT):
                        alert = self._kb_button(out, staff, code, parse_callback(data).appeal_id)
                    else:
                        alert, preview_text = self._appeal_button(out, staff, session, parse_callback(data))
            except WorkflowError as e:
                alert = error_text(e)
            except ValueError:
                alert = None  # tanınmayan düymə
        if preview_text is not None:
            self._preview(user_id, session, preview_text)
        self.store.save(user_id, session)
        return alert

    def _appeal_button(self, out: _Out, staff: Staff, session: StaffSession,
                       cb: cards.Callback) -> tuple[str | None, str | None]:
        """Qaytarır: (callback xəbərdarlığı, önizləmə üçün mətn)."""
        actor, s, appeal_id, version = actor_of(staff), out.s, cb.appeal_id, cb.version
        code = cb.code
        if code == ASSIGN:
            target = int(cb.extra)
            appeal = s.get(Appeal, appeal_id)
            if appeal is not None and appeal.status == AppealStatus.new:
                appeal = out.service.assign(actor, appeal_id, target, expected_version=version)
            else:
                appeal = out.service.reassign(actor, appeal_id, target, expected_version=version)
            out.say(f"✅ {appeal.number}: {s.get(Staff, target).full_name} təyin olundu.", appeal_id=appeal_id)
        elif code == OTHER:
            appeal = self._check(s, staff, appeal_id, version, Action.assign)
            self._staff_choice(out, appeal)
        elif code == ANSWER:
            appeal = self._check(s, staff, appeal_id, version, Action.send_answer)
            session.reset()
            self._outcome_menu(out, appeal)
        elif code == OUTCOME:
            appeal = self._check(s, staff, appeal_id, version, Action.send_answer)
            draft = session.extra.get("draft_text") if session.appeal_id == appeal_id else None
            session.reset()
            session.action, session.appeal_id, session.version = "answer_text", appeal_id, appeal.version
            session.outcome = Outcome(cb.extra).value
            if draft:
                return None, draft
            out.say(f"{appeal.number} · {OUTCOME_LABELS[Outcome(cb.extra)]}\n{PROMPTS['answer_text']}",
                    appeal_id=appeal_id)
        elif code == USE_DRAFT:
            appeal = self._check(s, staff, appeal_id, None, Action.send_answer)  # layihə düyməsi versiyasızdır
            draft = s.get(AppealMessage, int(cb.extra or 0))
            if draft is None or draft.appeal_id != appeal_id or draft.kind != MessageKind.ai_draft:
                raise NotFound("Layihə tapılmadı")
            if session.action == "answer_text" and session.appeal_id == appeal_id:
                return None, draft.text
            session.reset()
            session.appeal_id, session.version, session.extra["draft_text"] = appeal_id, appeal.version, draft.text
            self._outcome_menu(out, appeal)
        elif code == SEND:
            if session.action != "preview" or session.appeal_id != appeal_id:
                return STALE, None
            appeal = out.service.submit_answer(actor, appeal_id, outcome=Outcome(session.outcome), text=session.text,
                                               qa_critical=session.qa_critical, expected_version=session.version)
            session.reset()
            if appeal.status == AppealStatus.pending_approval:
                out.say(f"🟡 {appeal.number}: cavab koordinatorun təsdiqinə göndərildi.", appeal_id=appeal_id)
            else:
                out.say(f"✅ {appeal.number}: cavab vətəndaşa göndərildi.", appeal_id=appeal_id)
        elif code == REWRITE:
            if session.appeal_id != appeal_id or not session.outcome:
                return STALE, None
            session.action, session.text = "answer_text", None
            out.say(PROMPTS["answer_text"], appeal_id=appeal_id)
        elif code == ASK:
            self._pending(staff.telegram_user_id, session, "ask",
                          self._check(s, staff, appeal_id, version, Action.ask_citizen), out)
        elif code == RETURN:
            self._pending(staff.telegram_user_id, session, "return",
                          self._check(s, staff, appeal_id, version, Action.return_to_coordinator), out)
        elif code == SEND_BACK:
            self._pending(staff.telegram_user_id, session, "send_back",
                          self._check(s, staff, appeal_id, version, Action.send_back), out)
        elif code == REDIRECT:
            self._pending(staff.telegram_user_id, session, "redirect_target",
                          self._check(s, staff, appeal_id, version, Action.redirect), out)
        elif code == SKIP_LETTER:
            if session.action != "redirect_letter" or session.appeal_id != appeal_id:
                return STALE, None
            appeal = self._redirect(out, actor, session, None)
            session.reset()
            out.say(f"↪️ {appeal.number}: aidiyyəti üzrə göndərildi, vətəndaşa bildiriş gedir.", appeal_id=appeal_id)
        elif code == NOT_REVIEW:
            appeal = self._check(s, staff, appeal_id, version, Action.not_review)
            out.say(f"{appeal.number}: baxılmamış saxlamanın əsasını seçin (Qanunun 8.1-ci maddəsi):",
                    keyboard(*[[(label, callback(NOT_REVIEW_REASON, appeal_id, version, key))]
                               for key, (label, _) in NOT_REVIEW_REASONS.items()]), appeal_id=appeal_id)
        elif code == NOT_REVIEW_REASON:
            if cb.extra not in NOT_REVIEW_REASONS:
                raise ValidationFailed("Naməlum əsas")
            self._pending(staff.telegram_user_id, session, "not_review_text",
                          self._check(s, staff, appeal_id, version, Action.not_review), out, reason=cb.extra)
        elif code == EXTEND:
            appeal = self._check(s, staff, appeal_id, version, Action.extend)
            out.say(f"{appeal.number}: neçə iş günü uzadılsın? (m. 10.2: cəmi ən çoxu 30)",
                    keyboard([(f"{d} gün", callback(EXTEND_DAYS, appeal_id, version, d)) for d in EXTEND_OPTIONS]),
                    appeal_id=appeal_id)
        elif code == EXTEND_DAYS:
            self._pending(staff.telegram_user_id, session, "extend_reason",
                          self._check(s, staff, appeal_id, version, Action.extend), out, workdays=int(cb.extra))
        elif code == FIN:
            return f"FİN: {out.service.reveal_fin(actor, appeal_id)}", None
        elif code == ATTACHMENTS:
            appeal, ids = out.service.view_attachments(actor, appeal_id)
            for attachment_id in ids:  # fayl worker-də açılıb göndərilir, növbədə yalnız istinad var
                enqueue_document(s, bot=BotKind.staff, chat_id=staff.telegram_user_id, attachment_id=attachment_id,
                                 caption=appeal.number, appeal_id=appeal.id, now=out.now)
            return (f"📎 {len(ids)} fayl göndərilir" if ids else "Əlavə yoxdur"), None
        elif code == APPROVE:
            appeal = out.service.approve(actor, appeal_id, expected_version=version)
            out.say(f"✅ {appeal.number}: təsdiqləndi, cavab vətəndaşa göndərildi.", appeal_id=appeal_id)
        elif code == OPEN:
            appeal = s.get(Appeal, appeal_id)
            if appeal is None:
                raise NotFound("Müraciət tapılmadı")
            out.notifier.show_card(appeal, staff)
        return None, None

    def _live_chats(self, out: _Out) -> LiveChats:
        return LiveChats(out.s, now=out.now, templates=self.config.templates, hotline=self.hotline)

    def _kb_button(self, out: _Out, staff: Staff, code: str, entry_id: int) -> str:
        """Kurator təklifi: yalnız admin. Bilik bazasına yazmaq alınmasa, təklif gözləmədə qalır."""
        require_admin(actor_of(staff))
        entry = out.s.get(KbEntry, entry_id, with_for_update=True)
        if entry is None:
            raise NotFound("Təklif tapılmadı")
        if entry.status != "pending":
            return "Bu təklif üzrə artıq qərar verilib."
        if code == KB_APPROVE:
            if self.kb_add is None:
                return "Bilik bazası qoşulmayıb."
            try:
                self.kb_add(entry.id, entry.question, entry.answer)
            except Exception as e:  # embedding servisi və s.
                log.warning("bilik bazasına yazılmadı (%s)", type(e).__name__)
                return "Bilik bazasına yazılmadı, bir azdan yenidən cəhd edin."
            entry.status, decision = "approved", f"✅ Bilik bazasına əlavə olundu ({staff.full_name})"
        else:
            entry.status, decision = "rejected", f"❌ Rədd edildi ({staff.full_name})"
        entry.decided_by_staff_id, entry.decided_at = staff.id, out.now
        appeal = out.s.get(Appeal, entry.appeal_id) if entry.appeal_id else None
        for row in out.s.scalars(select(TelegramMessage).where(  # digər adminlərin kartları da bağlanır
                TelegramMessage.purpose == KB_PROPOSAL, TelegramMessage.appeal_id == entry.appeal_id,
                TelegramMessage.closed_at.is_(None))):
            enqueue_edit(out.s, bot=BotKind.staff, chat_id=row.chat_id, message_id=row.message_id, now=out.now,
                         rendered=cards.kb_proposal(entry, number=appeal.number if appeal else None,
                                                    decision=decision), appeal_id=entry.appeal_id, purpose=KB_PROPOSAL)
            row.closed_at = out.now
        return "✅ Bilik bazasına əlavə olundu." if entry.status == "approved" else "❌ Təklif rədd edildi."

    def _outcome_menu(self, out: _Out, appeal: Appeal) -> None:
        rows = [[(OUTCOME_LABELS[o], callback(OUTCOME, appeal.id, appeal.version, o.value))] for o in Outcome]
        out.say(f"{appeal.number}: cavabın nəticəsini seçin:", keyboard(*rows), appeal_id=appeal.id)

    def _staff_choice(self, out: _Out, appeal: Appeal) -> None:
        people = out.s.execute(
            select(Staff, Department.name).outerjoin(Department, Staff.department_id == Department.id)
            .where(Staff.is_active, Staff.role.in_([StaffRole.staff, StaffRole.coordinator]))
            .order_by(Staff.role.desc(), Department.name, Staff.full_name)  # əvvəl icraçılar
        ).all()
        if not people:
            out.say("Aktiv icraçı yoxdur.")
            return
        rows = [[(f"{person.full_name} · {dept or ROLE_LABELS[person.role]} (açıq: {open_appeal_count(out.s, person.id)})",
                  callback(ASSIGN, appeal.id, appeal.version, person.id))] for person, dept in people]
        out.say(f"{appeal.number}: icraçını seçin:", keyboard(*rows), appeal_id=appeal.id)

    # ================= əmrlər =================
    def _panel(self, out: _Out, staff: Staff) -> None:
        if staff.role not in (StaffRole.coordinator, StaffRole.head):
            out.say("Bu əmr yalnız koordinator və rəhbər üçündür.")
            return
        s, tz = out.s, self.calendar.tz
        today = out.now.astimezone(tz).date()
        day_start = datetime.combine(today, datetime.min.time(), tz)

        def count(*where) -> int:
            return s.scalar(select(func.count(Appeal.id)).where(*where))

        open_ = Appeal.status.in_(OPEN_STATUSES)
        soon = self.calendar.add_workdays(today, 3, count_start=False)
        lines = [
            f"📊 Panel · {today:%d.%m.%Y}",
            f"Yeni (təyin olunmayıb): {count(Appeal.status == AppealStatus.new)}",
            f"İcrada: {count(Appeal.status.in_([AppealStatus.assigned, AppealStatus.waiting_citizen]))}",
            f"  o cümlədən vətəndaşın cavabı gözlənilir: {count(Appeal.status == AppealStatus.waiting_citizen)}",
            f"Təsdiq gözləyir: {count(Appeal.status == AppealStatus.pending_approval)}",
            f"Təcili: {count(open_, Appeal.priority == Priority.urgent)}",
            f"Müddəti keçib: {count(open_, Appeal.due_date < today)}",
            f"3 iş günü içində bitir: {count(open_, Appeal.due_date >= today, Appeal.due_date <= soon)}",
            f"Bu gün daxil olub: {count(Appeal.created_at >= day_start)}",
        ]
        out.say("\n".join(lines))

    def _my_work(self, out: _Out, staff: Staff) -> None:
        appeals = out.s.scalars(select(Appeal).where(Appeal.assigned_staff_id == staff.id,
                                                     Appeal.status.in_(ACTIVE_WORK)).order_by(Appeal.due_date)).all()
        if not appeals:
            out.say("Açıq işiniz yoxdur.")
            return
        lines = [f"• {a.number} · {STAFF_STATUS_LABELS[a.status]} · son tarix {a.due_date:%d.%m.%Y} · {a.subject}"
                 for a in appeals]
        out.say("📋 İşləriniz:\n" + "\n".join(lines),
                keyboard(*[[(f"📂 {a.number}", callback(OPEN, a.id))] for a in appeals]))

    def _find(self, out: _Out, staff: Staff, number: str) -> None:
        appeal = out.s.scalar(select(Appeal).where(Appeal.number == number)) if number else None
        allowed = appeal is not None and (staff.role in (StaffRole.coordinator, StaffRole.head)
                                          or appeal.assigned_staff_id == staff.id)
        if not allowed:
            out.say("Müraciət tapılmadı. Nümunə: /tap MR-2026-000123")
            return
        out.say(f"📄 {appeal.number}: tam mətn\n\n{appeal.description}", appeal_id=appeal.id)
        out.notifier.show_card(appeal, staff)

    # ---------- admin: dəvət və deaktivasiya ----------
    def _invite_menu(self, out: _Out, staff: Staff) -> None:
        if not staff.is_admin:
            out.say("Bu əmr yalnız admin üçündür.")
            return
        out.say("Yeni əməkdaşın rolunu seçin:",
                keyboard([(ROLE_LABELS[r], f"{INVITE_ROLE}:{r.value}") for r in StaffRole]))

    def _staff_list(self, out: _Out, staff: Staff) -> None:
        if not staff.is_admin:
            out.say("Bu əmr yalnız admin üçündür.")
            return
        people = out.s.execute(
            select(Staff, Department.name).outerjoin(Department, Staff.department_id == Department.id)
            .where(Staff.is_active).order_by(Staff.role, Staff.full_name)
        ).all()
        lines = [f"• {p.full_name} · {ROLE_LABELS[p.role]}{' · ' + dept if dept else ''}"
                 f"{' · admin' if p.is_admin else ''} (açıq: {open_appeal_count(out.s, p.id)})" for p, dept in people]
        out.say("👥 Aktiv əməkdaşlar:\n" + "\n".join(lines) + "\n\nDeaktiv etmək üçün düyməni basın.",
                keyboard(*[[(f"🚫 {p.full_name}", f"{DEACTIVATE}:{p.id}")] for p, _ in people if p.id != staff.id]))

    def _admin_button(self, out: _Out, staff: Staff, code: str, arg: str) -> str | None:
        require_admin(actor_of(staff))
        if code == INVITE_ROLE:
            role = StaffRole(arg)
            if role == StaffRole.staff:
                departments = out.s.scalars(select(Department).where(Department.is_active)
                                            .order_by(Department.name)).all()
                out.say("Şöbəni seçin:", keyboard(*[[(d.name, f"{INVITE_DEPT}:{d.id}")] for d in departments]))
            else:
                self._create_invite(out, staff, role, None)
        elif code == INVITE_DEPT:
            self._create_invite(out, staff, StaffRole.staff, int(arg))
        elif code == DEACTIVATE:
            target = out.s.get(Staff, int(arg))
            released = out.service.deactivate_staff(actor_of(staff), int(arg))
            out.say(f"🚫 {target.full_name if target else '—'} deaktiv edildi. "
                    f"Koordinatora qaytarılan müraciətlər: {len(released)}.")
        return None

    def _create_invite(self, out: _Out, staff: Staff, role: StaffRole, department_id: int | None) -> None:
        token = create_invite(out.s, role=role, department_id=department_id, is_admin=False,
                              created_by_staff_id=staff.id, now=out.now)
        department = out.s.get(Department, department_id) if department_id else None
        out.say(f"🔗 Dəvət linki ({INVITE_HOURS} saat, bir dəfəlik):\n"
                f"{invite_link(self.bot_username, token)}\n"
                f"Rol: {ROLE_LABELS[role]}{' · şöbə: ' + department.name if department else ''}\n"
                "Linki yalnız həmin əməkdaşa göndərin.", purpose=SECRET_PURPOSE)

    def _register(self, out: _Out, sender: dict, token: str) -> None:
        user_id = sender.get("id")
        invite = out.s.scalar(select(StaffInvite).where(StaffInvite.token_hash == token_hash(token))
                              .with_for_update())
        if invite is None or invite.used_at is not None or invite.expires_at <= out.now:
            log.warning("əməkdaş botu: etibarsız dəvət linki (telegram_id=%s)", user_id)
            out.say("Dəvət linki etibarsızdır və ya vaxtı keçib. Adminə müraciət edin.")
            return
        existing = out.s.scalar(select(Staff).where(Staff.telegram_user_id == user_id))
        if existing is not None and existing.is_active:
            out.say(f"Siz artıq qeydiyyatdasınız. {HELP[existing.role]}")
            return
        name = " ".join(p for p in (sender.get("first_name"), sender.get("last_name")) if p) or f"Əməkdaş {user_id}"
        staff = existing or Staff(telegram_user_id=user_id)
        staff.full_name, staff.role, staff.department_id = name[:128], invite.role, invite.department_id
        staff.is_admin, staff.is_active = invite.is_admin, True
        out.s.add(staff)
        out.s.flush()
        invite.used_at, invite.used_by_staff_id = out.now, staff.id
        out.say(f"Xoş gəlmisiniz, {staff.full_name}! Rolunuz: {ROLE_LABELS[staff.role]}.\n{HELP[staff.role]}")
        creator = out.s.get(Staff, invite.created_by_staff_id) if invite.created_by_staff_id else None
        if creator is not None and creator.is_active:
            out.say(f"✅ {staff.full_name} əməkdaş botunda qeydiyyatdan keçdi ({ROLE_LABELS[staff.role]}).",
                    chat_id=creator.telegram_user_id)
