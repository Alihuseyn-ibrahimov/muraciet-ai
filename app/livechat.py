"""Real vaxtda operator çatı (spec 15, Mərhələ 3): vətəndaş botu ↔ əməkdaş botu.

Axın: vətəndaş iş vaxtında "💬 Operatorla danış" basır → bütün aktiv koordinatorlara "Qəbul et" kartı gedir →
ilk qəbul edən operator olur, gözləmə zamanı yazılanlar ona ötürülür → mesajlar hər iki tərəfə ötürülür →
hər tərəf /bitir ilə bağlayır. Qəbul edilməyən çat WAIT_MINUTES, yazışmasız aktiv çat IDLE_MINUTES sonra bağlanır
(n8n-in hər 5 dəqiqəlik yoxlaması və vətəndaşın növbəti mesajı ilə).

Vəziyyət bazadadır (live_chats), mesajlar chat_logs-a yazılır, göndəriş outbox ilədir. LLM iştirak etmir.
"""
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.tables import ChatLog, LiveChat, Staff
from app.notifier import recipients
from app.notifier.cards import LIVE_CHAT_ACCEPT, Rendered, callback, keyboard
from app.notifier.outbox import enqueue_rendered
from app.workflow.states import BotKind
from app.yaml_config import Templates

WAIT_MINUTES, IDLE_MINUTES = 10, 30
OPEN = ("waiting", "active")
LIVE_CHAT_CARD = "live_chat_card"
MAX_MESSAGE = 3500


class LiveChats:
    def __init__(self, s: Session, *, now: datetime, templates: Templates, hotline: str = ""):
        self.s, self.now, self.templates, self.hotline = s, now, templates, hotline

    # ---------- axtarış ----------
    def open_for_citizen(self, citizen_tg: int, *, lock: bool = False) -> LiveChat | None:
        query = select(LiveChat).where(LiveChat.citizen_telegram_id == citizen_tg, LiveChat.status.in_(OPEN))
        chat = self.s.scalar(query.with_for_update() if lock else query)
        if chat is not None and self._stale(chat):
            self._expire(chat)
            return None
        return chat

    def active_for_staff(self, staff_id: int) -> LiveChat | None:
        return self.s.scalar(select(LiveChat).where(LiveChat.staff_id == staff_id, LiveChat.status == "active"))

    # ---------- vətəndaş ----------
    def request(self, citizen_tg: int, *, citizen_name: str | None) -> LiveChat:
        chat = self.open_for_citizen(citizen_tg, lock=True)
        if chat is not None:
            return chat
        chat = LiveChat(citizen_telegram_id=citizen_tg, status="waiting", created_at=self.now,
                        last_activity_at=self.now)
        self.s.add(chat)
        self.s.flush()
        who = citizen_name or "profili olmayan vətəndaş"
        for coordinator in recipients.coordinators(self.s):
            self._to_staff(coordinator.telegram_user_id, Rendered(
                f"💬 Canlı çat sorğusu #{chat.id}: {who} operatorla danışmaq istəyir.\n"
                f"{WAIT_MINUTES} dəqiqə ərzində qəbul edilməsə, vətəndaşa qaynar xətt təklif olunacaq.",
                keyboard([("✅ Qəbul et", callback(LIVE_CHAT_ACCEPT, chat.id))])), purpose=LIVE_CHAT_CARD)
        self._to_citizen(chat, self.templates.render("live_chat_waiting"))
        return chat

    def from_citizen(self, chat: LiveChat, text: str) -> None:
        text = text[:MAX_MESSAGE]
        self.s.add(ChatLog(citizen_telegram_id=chat.citizen_telegram_id, role="citizen", content=text,
                           intent="live_chat", created_at=self.now))
        chat.last_activity_at = self.now
        if chat.status == "active":
            staff = self.s.get(Staff, chat.staff_id)
            self._to_staff(staff.telegram_user_id, Rendered(f"💬 Vətəndaş (#{chat.id}): {text}"))

    # ---------- operator ----------
    def accept(self, chat_id: int, staff: Staff) -> str:
        chat = self.s.get(LiveChat, chat_id, with_for_update=True)
        if chat is not None and chat.status == "waiting" and self._stale(chat):
            self._expire(chat)
        if chat is None or chat.status in ("closed", "expired"):
            return "Bu çat artıq bitib."
        if chat.status == "active":
            taken = self.s.get(Staff, chat.staff_id)
            return "Siz artıq bu çatdasınız." if taken.id == staff.id else f"Çatı artıq {taken.full_name} qəbul edib."
        if self.active_for_staff(staff.id) is not None:
            return "Əvvəlcə cari çatı /bitir ilə bağlayın."
        chat.status, chat.staff_id, chat.accepted_at, chat.last_activity_at = "active", staff.id, self.now, self.now
        backlog = list(self.s.scalars(select(ChatLog.content).where(
            ChatLog.citizen_telegram_id == chat.citizen_telegram_id, ChatLog.intent == "live_chat",
            ChatLog.role == "citizen", ChatLog.created_at >= chat.created_at).order_by(ChatLog.id)))
        lines = [f"✅ Çat #{chat.id} başladı. Yazdığınız hər mesaj vətəndaşa gedir. Bitirmək: /bitir"]
        if backlog:
            lines += ["", "Vətəndaş gözləyərkən yazıb:", *[f"💬 {m}" for m in backlog]]
        self._to_staff(staff.telegram_user_id, Rendered("\n".join(lines)))
        self._to_citizen(chat, self.templates.render("live_chat_connected"))
        return "Çat qəbul edildi."

    def from_staff(self, chat: LiveChat, staff: Staff, text: str) -> None:
        text = text[:MAX_MESSAGE]
        self.s.add(ChatLog(citizen_telegram_id=chat.citizen_telegram_id, role="operator", content=text,
                           intent="live_chat", created_at=self.now))
        chat.last_activity_at = self.now
        self._to_citizen(chat, f"👤 Operator: {text}")

    # ---------- bağlanma ----------
    def close(self, chat: LiveChat, *, by: str) -> None:
        chat.status, chat.closed_at, chat.closed_by = "closed", self.now, by
        self._to_citizen(chat, self.templates.render("live_chat_closed"))
        if chat.staff_id is not None:
            staff = self.s.get(Staff, chat.staff_id)
            reason = {"citizen": "vətəndaş bağladı", "staff": "siz bağladınız",
                      "timeout": f"{IDLE_MINUTES} dəqiqə yazışma olmadı"}[by]
            self._to_staff(staff.telegram_user_id, Rendered(f"⏹ Çat #{chat.id} bitdi ({reason})."))

    def sweep(self) -> dict:
        """n8n-in 5 dəqiqəlik yoxlaması: qəbul edilməyən və yazışmasız qalan çatlar."""
        expired = closed = 0
        for chat in self.s.scalars(select(LiveChat).where(LiveChat.status.in_(OPEN)).with_for_update()):
            if not self._stale(chat):
                continue
            if chat.status == "waiting":
                self._expire(chat)
                expired += 1
            else:
                self.close(chat, by="timeout")
                closed += 1
        return {"live_chats_expired": expired, "live_chats_closed": closed}

    # ---------- köməkçilər ----------
    def _stale(self, chat: LiveChat) -> bool:
        if chat.status == "waiting":
            return chat.created_at <= self.now - timedelta(minutes=WAIT_MINUTES)
        return (chat.last_activity_at or chat.created_at) <= self.now - timedelta(minutes=IDLE_MINUTES)

    def _expire(self, chat: LiveChat) -> None:
        if chat.status == "active":  # aktiv çat gözləmə müddəti ilə bitmir, yazışmasızlıqla bağlanır
            self.close(chat, by="timeout")
            return
        chat.status, chat.closed_at, chat.closed_by = "expired", self.now, "timeout"
        self._to_citizen(chat, self.templates.render("live_chat_expired",
                                                     hotline=f"\n{self.hotline}" if self.hotline else ""))

    def _to_staff(self, telegram_id: int, rendered: Rendered, purpose: str | None = None) -> None:
        enqueue_rendered(self.s, bot=BotKind.staff, chat_id=telegram_id, rendered=rendered, purpose=purpose,
                         now=self.now)

    def _to_citizen(self, chat: LiveChat, text: str) -> None:
        enqueue_rendered(self.s, bot=BotKind.citizen, chat_id=chat.citizen_telegram_id, rendered=Rendered(text),
                         now=self.now)
