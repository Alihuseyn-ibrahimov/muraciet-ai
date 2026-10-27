"""SLA monitoru (spec 11-ci bölmə, plan 8.1). n8n hər 5 dəqiqədən bir `POST /internal/sla/check` ilə çağırır
(bildirişlər təkrarlanmır, aşağıya bax; canlı çatın müddətləri də burada yoxlanılır).

| Hadisə                                                   | Kimə                         |
|----------------------------------------------------------|------------------------------|
| 4 iş saatı təyin olunmayıb (təcili: 1 təqvim saatı)      | aktiv koordinatorlar         |
| 8 iş saatı təyin olunmayıb (təcili: 2 təqvim saatı)      | rəhbər                       |
| Son tarixə 3 iş günü qalıb                               | icraçı                       |
| Son tarixə 1 iş günü qalıb                               | icraçı və koordinatorlar     |
| Müddət keçib                                             | hər gün rəhbərə (bir xülasə) |
| Vətəndaş əməkdaşın sualını 5 iş günü cavablamayıb        | vətəndaşa xatırlatma         |
| Vətəndaş 10 iş günü cavablamayıb                         | icraçıya iki yol (m. 8.1.5)  |
| Cavabdan 7 gün reaksiya yoxdur                           | avtomatik bağlanır           |

- Təcili müraciətlər üzrə bildirişlər 24/7, qalanları yalnız iş saatlarında gedir.
- Hər bildiriş `appeal_events`-ə `sla_notified` (qayda açarı ilə) yazılır və bir daha göndərilmir. Açarda
  son tarix və ya tarix var: müddət uzadılanda və ya yeni gündə bildiriş yenidən mümkündür.
- Mesajlarda şəxsi məlumat yoxdur, yalnız müraciətin nömrəsi və tarixlər.
"""
from collections.abc import Iterable
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.tables import Appeal, AppealEvent, AppealMessage, Citizen, Staff
from app.notifier import recipients
from app.notifier.cards import Rendered
from app.notifier.notifier import CITIZEN_QUESTION, STAFF_INFO, OutboxNotifier
from app.notifier.outbox import enqueue_rendered
from app.privacy.crypto import Crypto
from app.workflow.authz import Actor
from app.workflow.calendar import WorkCalendar
from app.workflow.service import AppealService
from app.workflow.states import AppealStatus, BotKind, MessageKind, Priority
from app.yaml_config import AppConfig

NOTIFIED = "sla_notified"
OPEN = (AppealStatus.new, AppealStatus.assigned, AppealStatus.waiting_citizen, AppealStatus.pending_approval)
IN_WORK = (AppealStatus.assigned, AppealStatus.waiting_citizen, AppealStatus.pending_approval)


class SlaMonitor:
    def __init__(self, sessions: sessionmaker[Session], *, config: AppConfig, calendar: WorkCalendar, crypto: Crypto,
                 org_name: str):
        self._sessions = sessions
        self.config, self.calendar, self.crypto, self.org_name = config, calendar, crypto, org_name

    def check(self, now: datetime) -> dict[str, int]:
        with self._sessions() as s, s.begin():
            run = _Run(s, self, now)
            run.unassigned()
            run.due_dates()
            run.overdue()
            run.waiting_citizen()
            notifier = OutboxNotifier(s, config=self.config, crypto=self.crypto, org_name=self.org_name,
                                      clock=lambda: now)
            closed = AppealService(s, config=self.config, calendar=self.calendar, crypto=self.crypto,
                                   notifier=notifier, clock=lambda: now).auto_close()
            return {"notified": run.notified, "auto_closed": closed}


class _Run:
    def __init__(self, s: Session, monitor: SlaMonitor, now: datetime):
        self.s, self.m, self.now = s, monitor, now
        self.cal, self.esc, self.timers = monitor.calendar, monitor.config.sla.escalation, monitor.config.sla.citizen
        self.today = now.astimezone(self.cal.tz).date()
        self.work_time = self.cal.is_work_time(now)
        self.notified = 0

    # ---------- köməkçilər ----------
    def _done(self, appeal: Appeal, key: str) -> bool:
        return self.s.scalar(select(AppealEvent.id).where(
            AppealEvent.appeal_id == appeal.id, AppealEvent.action == NOTIFIED,
            AppealEvent.payload["key"].astext == key).limit(1)) is not None

    def _mark(self, appeal: Appeal, rule: str, key: str) -> None:
        system = Actor.system()
        self.s.add(AppealEvent(appeal_id=appeal.id, actor_type=system.type, actor_id=None, action=NOTIFIED,
                               from_status=appeal.status, to_status=appeal.status,
                               payload={"rule": rule, "key": key}, created_at=self.now))
        self.notified += 1

    def _send(self, chat_ids: Iterable[int], text: str, appeal: Appeal | None, *, bot=BotKind.staff,
              purpose: str = STAFF_INFO) -> None:
        for chat_id in dict.fromkeys(chat_ids):
            enqueue_rendered(self.s, bot=bot, chat_id=chat_id, rendered=Rendered(text),
                             appeal_id=appeal.id if appeal else None, purpose=purpose, now=self.now)

    def _may_send(self, appeal: Appeal) -> bool:
        return appeal.priority == Priority.urgent or self.work_time

    def _staff(self, staff_id: int | None) -> list[int]:
        staff = self.s.get(Staff, staff_id) if staff_id else None
        return [staff.telegram_user_id] if staff is not None and staff.is_active else []

    def _coordinators(self) -> list[int]:
        return [c.telegram_user_id for c in recipients.coordinators(self.s)]

    def _heads(self) -> list[int]:
        return [h.telegram_user_id for h in recipients.heads(self.s)]

    def _workdays_after(self, start: date, end: date) -> int:
        """(start, end] aralığındakı iş günləri; end < start olarsa mənfi."""
        if end < start:
            return -self._workdays_after(end, start)
        days, day = 0, start
        while day < end:
            day += timedelta(days=1)
            days += self.cal.is_workday(day)
        return days

    def _new_since(self, appeal: Appeal) -> datetime:
        """Müraciətin son dəfə 'new' vəziyyətinə keçdiyi an (yaranma, yenidən açılma, qaytarılma)."""
        entered = self.s.scalar(select(AppealEvent.created_at).where(
            AppealEvent.appeal_id == appeal.id, AppealEvent.to_status == AppealStatus.new,
            (AppealEvent.from_status.is_(None)) | (AppealEvent.from_status != AppealStatus.new),
        ).order_by(AppealEvent.id.desc()).limit(1))
        return entered or appeal.created_at

    # ---------- qaydalar ----------
    def unassigned(self) -> None:
        for appeal in self.s.scalars(select(Appeal).where(Appeal.status == AppealStatus.new).order_by(Appeal.id)):
            if not self._may_send(appeal):
                continue
            since = self._new_since(appeal)
            if appeal.priority == Priority.urgent:
                hours = (self.now - since).total_seconds() / 3600
                levels = ((self.esc.urgent_unassigned_coordinator_hours, "coordinator"),
                          (self.esc.urgent_unassigned_head_hours, "head"))
                unit = "saat"
            else:
                hours = self.cal.work_hours_between(since, self.now)
                levels = ((self.esc.unassigned_coordinator_work_hours, "coordinator"),
                          (self.esc.unassigned_head_work_hours, "head"))
                unit = "iş saatı"
            for limit, level in levels:
                key = f"unassigned_{level}:{since.isoformat()}"
                if hours >= limit and not self._done(appeal, key):
                    who = self._coordinators() if level == "coordinator" else self._heads()
                    note = "" if level == "coordinator" else " (rəhbərə eskalasiya)"
                    self._send(who, f"⏰ {appeal.number}: {limit} {unit}dır təyin olunmayıb{note}.\n"
                                    f"Bax: /tap {appeal.number}", appeal)
                    self._mark(appeal, f"unassigned_{level}", key)

    def due_dates(self) -> None:
        reminders = sorted(self.esc.due_reminder_workdays)  # [1, 3]
        for appeal in self.s.scalars(select(Appeal).where(Appeal.status.in_(IN_WORK)).order_by(Appeal.id)):
            if not self._may_send(appeal):
                continue
            left = self._workdays_after(self.today, appeal.due_date)
            applicable = [d for d in reminders if 0 <= left <= d]
            if not applicable:
                continue
            # Yalnız ən kiçik uyğun hədd göndərilir; böyüklər "göndərilmiş" sayılır. Yoxlamalar buraxılıbsa
            # (n8n dayanıb), son gün "3 gün qalıb" yazılmır və iki mesaj getmir
            days, *passed = applicable
            key = f"due_{days}:{appeal.due_date.isoformat()}"
            if not self._done(appeal, key):
                who = self._staff(appeal.assigned_staff_id)
                if days == reminders[0]:
                    who += self._coordinators()
                icon = "⚠️" if days == reminders[0] else "📅"
                when = f"son tarixə {left} iş günü qalıb" if left else "bu gün son tarixdir"
                self._send(who, f"{icon} {appeal.number}: {when} ({appeal.due_date:%d.%m.%Y}).", appeal)
                self._mark(appeal, f"due_{days}", key)
            for bigger in passed:
                key = f"due_{bigger}:{appeal.due_date.isoformat()}"
                if not self._done(appeal, key):
                    self._mark(appeal, f"due_{bigger}", key)

    def overdue(self) -> None:
        if not self.work_time:
            return
        late = [a for a in self.s.scalars(select(Appeal).where(Appeal.status.in_(OPEN), Appeal.due_date < self.today)
                                          .order_by(Appeal.due_date, Appeal.id))
                if not self._done(a, f"overdue:{self.today.isoformat()}")]
        if not late:
            return
        lines = []
        for appeal in late:
            assignee = self.s.get(Staff, appeal.assigned_staff_id) if appeal.assigned_staff_id else None
            lines.append(f"• {appeal.number} · son tarix {appeal.due_date:%d.%m.%Y} · "
                         f"{assignee.full_name if assignee else 'təyin olunmayıb'}")
            self._mark(appeal, "overdue", f"overdue:{self.today.isoformat()}")
        self._send(self._heads(), f"🔴 Müddəti keçmiş müraciətlər ({len(late)}):\n" + "\n".join(lines), None)

    def waiting_citizen(self) -> None:
        templates = self.m.config.templates
        for appeal in self.s.scalars(select(Appeal).where(Appeal.status == AppealStatus.waiting_citizen)):
            if not self._may_send(appeal):
                continue
            question = self.s.scalar(select(AppealMessage).where(
                AppealMessage.appeal_id == appeal.id, AppealMessage.kind == MessageKind.staff_question,
            ).order_by(AppealMessage.id.desc()).limit(1))
            if question is None:
                continue
            asked = question.created_at.astimezone(self.cal.tz).date()
            waited = self._workdays_after(asked, self.today)
            key = f"waiting_reminder:{question.id}"
            if waited >= self.timers.waiting_reminder_workdays and not self._done(appeal, key):
                citizen = self.s.get(Citizen, appeal.citizen_id)
                self._send([citizen.telegram_user_id],
                           templates.render("waiting_reminder", number=appeal.number, question=question.text),
                           appeal, bot=BotKind.citizen, purpose=CITIZEN_QUESTION)  # reply bu mesaja da bağlanır
                self._mark(appeal, "waiting_reminder", key)
            key = f"waiting_staff:{question.id}"
            if waited >= self.timers.waiting_staff_prompt_workdays and not self._done(appeal, key):
                self._send(self._staff(appeal.assigned_staff_id),
                           f"❗ {appeal.number}: vətəndaş {waited} iş günüdür sualınıza cavab vermir.\n"
                           "İki yol var: mövcud məlumatla cavab verin (✍️ Cavab yaz) və ya müraciəti m. 8.1.5 üzrə "
                           "baxılmamış saxlamaq üçün koordinatora qaytarın (↩️).", appeal)
                self._mark(appeal, "waiting_staff", key)
