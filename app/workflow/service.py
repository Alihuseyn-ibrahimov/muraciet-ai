"""Müraciətin həyat dövrü (spec 7-ci bölmə). Müraciətin vəziyyəti yalnız bu servis vasitəsilə dəyişir.

Hər əməliyyat eyni ardıcıllıqla işləyir:
1. müraciət SELECT ... FOR UPDATE ilə yüklənir, ona görə eyni müraciət üzrə iki əməliyyat növbəyə düşür;
2. icazə (authz) və düymənin versiyası yoxlanılır, köhnə düymə StaleAppeal verir;
3. yeni vəziyyəti machine.py müəyyən edir;
4. dəyişikliklər tətbiq olunur və versiya artır;
5. audit jurnalına (appeal_events) qeyd yazılır;
6. hadisə Notifier-ə ötürülür. Faza 4-də bu, eyni tranzaksiyada outbox-a yazılır.

Tranzaksiyanı çağıran idarə edir: xəta olarsa heç nə yazılmır, nə vəziyyət, nə audit, nə bildiriş.
Vaxt `clock`-dan alınır, ona görə testlərdə istənilən anı yoxlamaq olur.
"""
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.tables import Appeal, AppealEvent, AppealMessage, Attachment, Citizen, Department, Job, Staff
from app.privacy.crypto import Crypto
from app.workflow.approval import needs_approval
from app.workflow.authz import Actor, authorize, require_admin
from app.workflow.calendar import WorkCalendar
from app.workflow.errors import NotFound, StaleAppeal, ValidationFailed
from app.workflow.machine import Action
from app.workflow.machine import next_status
from app.workflow.states import (
    STAFF_STATUS_LABELS,
    AppealStatus,
    Category,
    MessageKind,
    Outcome,
    Priority,
    StaffRole,
)
from app.workflow.suggest import suggest_assignee
from app.yaml_config import AppConfig

MAX_TEXT = 4000  # Telegram mesajının limiti 4096 simvoldur, imza və başlıq üçün yer saxlanılır
MAX_SUBJECT = 200
PLACEHOLDER = re.compile(r"\[\s*(?:…|(?:\.\s*){2,})\s*\]")


class Notifier(Protocol):
    def appeal_event(self, appeal: Appeal, event: str, **details: Any) -> None: ...


@dataclass(frozen=True)
class TriageOutcome:
    category: Category
    priority: Priority
    legal_issue: bool


@dataclass(frozen=True)
class RoutingOutcome:
    department_code: str
    reason: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _snapshot(appeal: Appeal) -> dict:
    return {
        "status": appeal.status,
        "assigned_staff_id": appeal.assigned_staff_id,
        "version": appeal.version,
        "number": appeal.number,
    }


def has_placeholder(text: str) -> bool:
    """AI layihəsinin doldurulmamış yeri: "[...]", "[…]", "[. . .]". Belə cavab vətəndaşa getmir."""
    return bool(PLACEHOLDER.search(text or ""))


def _clean(text: str, field: str, limit: int = MAX_TEXT) -> str:
    value = (text or "").strip()
    if not value:
        raise ValidationFailed(f"{field} boş ola bilməz")
    if len(value) > limit:
        raise ValidationFailed(f"{field} {limit} simvoldan uzun ola bilməz")
    return value


class AppealService:
    def __init__(
        self,
        session: Session,
        *,
        config: AppConfig,
        calendar: WorkCalendar,
        crypto: Crypto,
        notifier: Notifier,
        clock: Callable[[], datetime] = utc_now,
    ):
        self.session = session
        self.config = config
        self.calendar = calendar
        self.crypto = crypto
        self.notifier = notifier
        self._clock = clock

    # ================= köməkçilər =================
    def _now(self) -> datetime:
        return self._clock()

    def _load(self, appeal_id: int, *, lock: bool = True) -> Appeal:
        stmt = select(Appeal).where(Appeal.id == appeal_id).execution_options(populate_existing=True)
        if lock:
            stmt = stmt.with_for_update()
        appeal = self.session.scalar(stmt)
        if appeal is None:
            raise NotFound("Müraciət tapılmadı")
        return appeal

    def _apply(
        self,
        appeal: Appeal,
        actor: Actor,
        action: Action,
        *,
        expected_version: int | None = None,
        payload: dict | None = None,
    ) -> AppealStatus:
        """İcazə → versiya → keçid → audit. Köhnə vəziyyəti qaytarır."""
        authorize(actor, action, appeal)
        if expected_version is not None and appeal.version != expected_version:
            raise StaleAppeal(
                f"Bu əməliyyat artıq köhnəlib: müraciətin cari vəziyyəti "
                f"'{STAFF_STATUS_LABELS[appeal.status]}'",
                _snapshot(appeal),
            )
        old = appeal.status
        appeal.status = next_status(old, action)
        appeal.version += 1
        self._event(appeal, actor, action, old, appeal.status, payload)
        return old

    def _transition(self, actor: Actor, appeal_id: int, action: Action, **kwargs) -> Appeal:
        appeal = self._load(appeal_id)
        self._apply(appeal, actor, action, **kwargs)
        return appeal

    def _event(self, appeal: Appeal | None, actor: Actor, action: Action | str, from_status, to_status,
               payload: dict | None) -> None:
        self.session.add(AppealEvent(
            appeal_id=appeal.id if appeal else None,
            actor_type=actor.type,
            actor_id=actor.id,
            action=str(action),
            from_status=from_status,
            to_status=to_status,
            payload=payload,
            created_at=self._now(),
        ))

    def _message(self, appeal: Appeal, kind: MessageKind, text: str, *, author: Actor | None = None,
                 meta: dict | None = None) -> AppealMessage:
        message = AppealMessage(
            appeal_id=appeal.id, kind=kind, text=text, meta=meta, created_at=self._now(),
            author_staff_id=author.id if author and author.role else None,
        )
        self.session.add(message)
        self.session.flush()
        return message

    def _job(self, kind: str, appeal: Appeal) -> None:
        self.session.add(Job(kind=kind, appeal_id=appeal.id))

    def _assignable(self, staff_id: int) -> Staff:
        staff = self.session.get(Staff, staff_id)
        if staff is None or not staff.is_active or staff.role not in (StaffRole.staff, StaffRole.coordinator):
            raise ValidationFailed("Bu əməkdaşa müraciət təyin etmək olmaz (deaktivdir və ya rolu uyğun deyil)")
        return staff

    def _department(self, code: str | None) -> tuple[Department, bool]:
        """(şöbə, tapıldımı). Naməlum və ya deaktiv kod gələrsə Ümumi şöbə qaytarılır."""
        if code:
            found = self.session.scalar(select(Department).where(Department.code == code, Department.is_active))
            if found:
                return found, True
        return self.session.scalar(select(Department).where(Department.code == "general")), False

    def _mark_answered(self, appeal: Appeal) -> None:
        now = self._now()
        appeal.answered_at = now
        appeal.answer_work_hours = round(self.calendar.work_hours_between(appeal.created_at, now), 2)

    def _latest_answer(self, appeal: Appeal) -> AppealMessage | None:
        return self.session.scalar(
            select(AppealMessage)
            .where(AppealMessage.appeal_id == appeal.id, AppealMessage.kind == MessageKind.final_answer)
            .order_by(AppealMessage.id.desc())
            .limit(1)
        )

    # ================= qeydiyyat və triaj =================
    def create_appeal(self, citizen_id: int, *, subject: str, summary: str, description: str) -> Appeal:
        citizen = self.session.get(Citizen, citizen_id)
        if citizen is None:
            raise NotFound("Vətəndaş tapılmadı")
        required = [
            ("soyad", citizen.last_name), ("ad", citizen.first_name), ("ata adı", citizen.patronymic),
            ("ünvan", citizen.address_enc), ("telefon", citizen.phone_enc), ("FİN", citizen.fin_enc),
            ("razılıq", citizen.consent_at),
        ]
        missing = [label for label, value in required if not value]
        if missing:  # m. 6.3.2 və 6.4: anonim müraciət qəbul edilmir
            raise ValidationFailed(f"Müraciət qeydə alına bilməz, çatışmır: {', '.join(missing)}")

        now = self._now()
        due = self.calendar.legal_due_date(now)
        appeal = Appeal(
            citizen_id=citizen.id,
            subject=_clean(subject, "Mövzu", MAX_SUBJECT),
            summary=_clean(summary, "Xülasə"),
            description=_clean(description, "Təsvir"),
            status=AppealStatus.new,
            due_date=due,
            original_due_date=due,
            created_at=now,
            version=1,
        )
        self.session.add(appeal)
        self.session.flush()
        appeal.number = f"MR-{now.astimezone(self.calendar.tz).year}-{appeal.id:06d}"
        self._event(appeal, Actor.citizen(citizen.id), Action.create, None, AppealStatus.new, None)
        self._job("triage", appeal)
        return appeal

    def apply_triage(self, appeal_id: int, triage: TriageOutcome | None, routing: RoutingOutcome | None) -> Appeal:
        """Triaj və Yönləndirmə agentlərinin nəticəsi. None: agent xəta verib, təhlükəsiz standart tətbiq olunur."""
        appeal = self._load(appeal_id)
        if appeal.category is not None:  # artıq triaj olunub (iş təkrar işə düşsə belə ikinci kart getmir)
            return appeal

        department, found = self._department(routing.department_code if routing else None)
        category = triage.category if triage else Category.request
        priority = triage.priority if triage else Priority.normal
        payload = {"category": category, "priority": priority, "department": department.code,
                   "triage_failed": triage is None, "routing_failed": not found}
        self._apply(appeal, Actor.system(), Action.apply_triage, payload=payload)

        appeal.category, appeal.priority = category, priority
        appeal.legal_issue = triage.legal_issue if triage else False
        appeal.triage_failed = triage is None
        appeal.department_id = department.id
        appeal.ai_department_reason = routing.reason if routing and found else "AI yönləndirməsi alınmadı: Ümumi şöbə"
        if priority == Priority.urgent:  # m. 10.4
            appeal.urgent_deadline = self.calendar.urgent_deadline(appeal.created_at)

        suggestion = suggest_assignee(self.session, department.id)
        self.notifier.appeal_event(appeal, "triaged", suggested_staff_id=suggestion.id if suggestion else None)
        return appeal

    # ================= koordinator =================
    def assign(self, actor: Actor, appeal_id: int, staff_id: int, *, expected_version: int | None) -> Appeal:
        staff = self._assignable(staff_id)
        appeal = self._transition(actor, appeal_id, Action.assign, expected_version=expected_version,
                                  payload={"staff_id": staff_id})
        now = self._now()
        appeal.assigned_staff_id = staff.id
        if appeal.first_assigned_at is None:
            appeal.first_assigned_at = now
            appeal.assign_work_hours = round(self.calendar.work_hours_between(appeal.created_at, now), 2)
        staff.last_assigned_at = now
        self._job("draft", appeal)  # Cavab layihəsi agenti
        self.notifier.appeal_event(appeal, "assigned", staff_id=staff.id)
        return appeal

    def reassign(self, actor: Actor, appeal_id: int, staff_id: int, *, expected_version: int | None) -> Appeal:
        staff = self._assignable(staff_id)
        appeal = self._load(appeal_id)
        previous = appeal.assigned_staff_id
        if previous == staff.id:
            raise ValidationFailed("Müraciət artıq bu əməkdaşa təyin olunub")
        self._apply(appeal, actor, Action.reassign, expected_version=expected_version,
                    payload={"from_staff_id": previous, "staff_id": staff.id})
        appeal.assigned_staff_id = staff.id
        staff.last_assigned_at = self._now()
        self.notifier.appeal_event(appeal, "reassigned", previous_staff_id=previous, staff_id=staff.id)
        return appeal

    def redirect(self, actor: Actor, appeal_id: int, *, target: str, letter_ref: str | None = None,
                 expected_version: int | None) -> Appeal:
        target = _clean(target, "Qurumun adı", 255)
        appeal = self._transition(actor, appeal_id, Action.redirect, expected_version=expected_version,
                                  payload={"target": target, "letter_ref": letter_ref})
        appeal.redirect_target, appeal.redirect_letter_ref = target, (letter_ref or None)
        appeal.closed_at = self._now()
        self.notifier.appeal_event(appeal, "redirected", target=target, letter_ref=letter_ref)
        return appeal

    def not_review(self, actor: Actor, appeal_id: int, *, reason: str, expected_version: int | None) -> Appeal:
        reason = _clean(reason, "Səbəb")
        appeal = self._transition(actor, appeal_id, Action.not_review, expected_version=expected_version,
                                  payload={"reason": reason})
        appeal.not_reviewed_reason = reason
        appeal.closed_at = self._now()
        self.notifier.appeal_event(appeal, "not_reviewed", reason=reason)
        return appeal

    def extend(self, actor: Actor, appeal_id: int, *, workdays: int, reason: str,
               expected_version: int | None) -> Appeal:
        reason = _clean(reason, "Səbəb")
        if workdays < 1:
            raise ValidationFailed("Uzatma ən azı 1 iş günü olmalıdır")
        appeal = self._load(appeal_id)
        new_due = self.calendar.add_workdays(appeal.due_date, workdays, count_start=False)
        limit = self.calendar.add_workdays(
            appeal.original_due_date, self.config.sla.legal.max_extension_workdays, count_start=False
        )
        if new_due > limit:
            raise ValidationFailed(
                f"Uzatma qanunla icazə veriləndən çoxdur (m. 10.2): son tarix ən gec {limit:%d.%m.%Y} ola bilər"
            )
        self._apply(appeal, actor, Action.extend, expected_version=expected_version,
                    payload={"workdays": workdays, "due_date": new_due.isoformat(), "reason": reason})
        appeal.due_date, appeal.extension_reason = new_due, reason
        self.notifier.appeal_event(appeal, "extended", due_date=new_due, reason=reason)
        return appeal

    def approve(self, actor: Actor, appeal_id: int, *, expected_version: int | None) -> Appeal:
        appeal = self._transition(actor, appeal_id, Action.approve, expected_version=expected_version)
        self._mark_answered(appeal)
        answer = self._latest_answer(appeal)
        self.notifier.appeal_event(appeal, "answer_sent", message_id=answer.id if answer else None)
        return appeal

    def send_back(self, actor: Actor, appeal_id: int, comment: str, *, expected_version: int | None) -> Appeal:
        comment = _clean(comment, "Şərh")
        appeal = self._transition(actor, appeal_id, Action.send_back, expected_version=expected_version,
                                  payload={"comment": comment})
        self._message(appeal, MessageKind.coordinator_comment, comment, author=actor)
        assignee = self.session.get(Staff, appeal.assigned_staff_id) if appeal.assigned_staff_id else None
        if assignee is None or not assignee.is_active:  # icraçı bu arada deaktiv edilib
            self._apply(appeal, Actor.system(), Action.release, payload={"staff_id": appeal.assigned_staff_id})
            appeal.assigned_staff_id = None
            self.notifier.appeal_event(appeal, "released", comment=comment)
            return appeal
        self.notifier.appeal_event(appeal, "sent_back", comment=comment)
        return appeal

    # ================= icraçı =================
    def return_to_coordinator(self, actor: Actor, appeal_id: int, comment: str, *,
                              expected_version: int | None) -> Appeal:
        comment = _clean(comment, "Şərh")
        appeal = self._load(appeal_id)
        previous = appeal.assigned_staff_id
        self._apply(appeal, actor, Action.return_to_coordinator, expected_version=expected_version,
                    payload={"comment": comment, "staff_id": previous})
        appeal.assigned_staff_id = None
        self.notifier.appeal_event(appeal, "returned", comment=comment, previous_staff_id=previous)
        return appeal

    def ask_citizen(self, actor: Actor, appeal_id: int, question: str, *, expected_version: int | None) -> Appeal:
        question = _clean(question, "Sual")
        appeal = self._transition(actor, appeal_id, Action.ask_citizen, expected_version=expected_version)
        message = self._message(appeal, MessageKind.staff_question, question, author=actor)
        self.notifier.appeal_event(appeal, "question_to_citizen", message_id=message.id, question=question)
        return appeal

    def submit_answer(self, actor: Actor, appeal_id: int, *, outcome: Outcome, text: str,
                      qa_critical: bool | None, expected_version: int | None) -> Appeal:
        """Cavab ya birbaşa vətəndaşa gedir, ya da seçmə təsdiq qaydalarına görə koordinatora."""
        text = _clean(text, "Cavab")
        if has_placeholder(text):  # təsdiq yolu da daxil: yarımçıq layihə heç vaxt göndərilmir
            raise ValidationFailed("Cavabda doldurulmamış yer var ([...]). Mətni tamamlayın")
        appeal = self._load(appeal_id)
        decision = needs_approval(appeal, outcome=outcome, qa_critical=qa_critical, actor=actor,
                                  rules=self.config.approval)
        action = Action.request_approval if decision.required else Action.send_answer
        self._apply(appeal, actor, action, expected_version=expected_version,
                    payload={"outcome": outcome, "approval_reasons": decision.reasons})
        appeal.outcome, appeal.needs_approval = outcome, decision.required
        message = self._message(appeal, MessageKind.final_answer, text, author=actor, meta={
            "outcome": outcome.value, "approval_reasons": decision.reasons, "qa_critical": qa_critical,
        })
        if decision.required:
            self.notifier.appeal_event(appeal, "approval_requested", message_id=message.id, reasons=decision.reasons)
        else:
            self._mark_answered(appeal)
            self.notifier.appeal_event(appeal, "answer_sent", message_id=message.id)
        return appeal

    # ================= vətəndaş =================
    def citizen_reply(self, citizen_id: int, appeal_id: int, text: str) -> Appeal:
        text = _clean(text, "Mesaj")
        appeal = self._transition(Actor.citizen(citizen_id), appeal_id, Action.citizen_reply)
        message = self._message(appeal, MessageKind.citizen_reply, text)
        self.notifier.appeal_event(appeal, "citizen_replied", message_id=message.id)
        return appeal

    def citizen_feedback(self, citizen_id: int, appeal_id: int, *, satisfied: bool,
                         reason: str | None = None) -> Appeal:
        actor = Actor.citizen(citizen_id)
        if satisfied:
            appeal = self._transition(actor, appeal_id, Action.feedback_positive)
            appeal.satisfaction, appeal.closed_at = 1, self._now()
            self._job("curate", appeal)  # kurator agenti bilik bazası üçün təklif hazırlayır (admin təsdiqləyir)
            return appeal

        reason = (reason or "").strip()[:MAX_TEXT] or None
        appeal = self._transition(actor, appeal_id, Action.feedback_negative, payload={"reason": reason})
        appeal.satisfaction = -1
        appeal.reopen_count += 1
        appeal.assigned_staff_id = None  # koordinatorun növbəsinə qayıdır
        appeal.outcome, appeal.needs_approval = None, False
        if reason:
            self._message(appeal, MessageKind.citizen_reply, reason, meta={"feedback": "negative"})
        self.notifier.appeal_event(appeal, "reopened", reason=reason)
        return appeal

    # ================= sistem və admin =================
    def auto_close(self) -> int:
        """Cavabdan sonra auto_close_days gün reaksiya olmayan müraciətləri bağlayır."""
        now = self._now()
        cutoff = now - timedelta(days=self.config.sla.citizen.auto_close_days)
        ids = self.session.scalars(
            select(Appeal.id).where(Appeal.status == AppealStatus.answered, Appeal.answered_at <= cutoff)
        ).all()
        for appeal_id in ids:
            appeal = self._transition(Actor.system(), appeal_id, Action.auto_close)
            appeal.closed_at = now
        return len(ids)

    def deactivate_staff(self, actor: Actor, staff_id: int) -> list[int]:
        """Əməkdaşı deaktiv edir. Onun icradakı müraciətləri koordinatorun növbəsinə qayıdır.
        Təsdiq gözləyən cavablar qalır, çünki cavab artıq yazılıb."""
        require_admin(actor)
        if staff_id == actor.id:
            raise ValidationFailed("Özünüzü deaktiv edə bilməzsiniz")
        staff = self.session.get(Staff, staff_id)
        if staff is None:
            raise NotFound("Əməkdaş tapılmadı")
        if not staff.is_active:
            return []
        staff.is_active = False
        self._event(None, actor, "staff_deactivated", None, None, {"staff_id": staff_id})

        released = []
        appeals = self.session.scalars(
            select(Appeal)
            .where(Appeal.assigned_staff_id == staff_id,
                   Appeal.status.in_([AppealStatus.assigned, AppealStatus.waiting_citizen]))
            .with_for_update()
        ).all()
        for appeal in appeals:
            self._apply(appeal, Actor.system(), Action.release, payload={"staff_id": staff_id})
            appeal.assigned_staff_id = None
            self.notifier.appeal_event(appeal, "released", staff_id=staff_id)
            released.append(appeal.id)
        return released

    def reveal_fin(self, actor: Actor, appeal_id: int) -> str:
        """FİN-i göstərir. Hər baxış audit jurnalına yazılır, dəyərin özü yazılmır."""
        appeal = self._load(appeal_id, lock=False)
        authorize(actor, Action.reveal_fin, appeal)
        citizen = self.session.get(Citizen, appeal.citizen_id)
        self._event(appeal, actor, "pii_viewed", appeal.status, appeal.status, {"field": "fin"})
        return self.crypto.decrypt(citizen.fin_enc)

    def view_attachments(self, actor: Actor, appeal_id: int) -> tuple[Appeal, list[int]]:
        """Vətəndaşın əlavələri (id-lər). Baxış audit jurnalına yazılır, faylı outbox göndərir."""
        appeal = self._load(appeal_id, lock=False)
        authorize(actor, Action.view_attachments, appeal)
        ids = list(self.session.scalars(select(Attachment.id).where(Attachment.appeal_id == appeal.id)
                                        .order_by(Attachment.id)))
        if ids:
            self._event(appeal, actor, "pii_viewed", appeal.status, appeal.status,
                        {"field": "attachments", "count": len(ids)})
        return appeal, ids
