"""Servisin hadisələrini outbox mesajlarına çevirir (spec 6-cı və 7-ci bölmələr).

AppealService "nə baş verdi" deyir (məs. "assigned"), bu modul isə kimə hansı kartın və ya mətnin
gedəcəyini müəyyən edir. Bütün yazılar servisin tranzaksiyasında olur: vəziyyət dəyişikliyi və
mesajlar birlikdə ya yazılır, ya da heç biri yazılmır.

Kartların yenilənməsi (refresh_cards): düymələr müraciətin versiyasını daşıyır, hər əməliyyat isə
versiyanı artırır. Ona görə hər hadisədən sonra hələ aktiv olan kartlar yeni versiya ilə yenilənir,
aktivliyini itirənlərin düymələri silinir və onlar bir daha yenilənmir (closed_at).
"""
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.tables import Appeal, AppealMessage, Attachment, Citizen, Department, Staff, TelegramMessage
from app.notifier import cards, recipients
from app.notifier.cards import CitizenView, Rendered, Suggestion
from app.notifier.outbox import enqueue_edit, enqueue_rendered
from app.privacy.crypto import Crypto
from app.workflow.states import AppealStatus, BotKind, MessageKind, StaffRole
from app.workflow.suggest import open_appeal_count, suggest_assignee
from app.yaml_config import AppConfig

COORDINATOR_CARD, STAFF_CARD, APPROVAL_CARD = "coordinator_card", "staff_card", "approval_card"
CARD_PURPOSES = (COORDINATOR_CARD, STAFF_CARD, APPROVAL_CARD)
CITIZEN_QUESTION, CITIZEN_ANSWER, CITIZEN_NOTICE = "citizen_question", "citizen_answer", "citizen_notice"
STAFF_INFO, ANSWER_TEXT = "staff_info", "answer_text"
STAFF_ACTIVE = (AppealStatus.assigned, AppealStatus.waiting_citizen)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def short_name(staff: Staff) -> str:
    parts = staff.full_name.split()
    return f"{parts[0]} {parts[1][0]}." if len(parts) > 1 else staff.full_name


class OutboxNotifier:
    def __init__(self, session: Session, *, config: AppConfig, crypto: Crypto, org_name: str,
                 clock: Callable[[], datetime] = utc_now):
        self.session = session
        self.config = config
        self.crypto = crypto
        self.org_name = org_name
        self._clock = clock

    # ---------- giriş nöqtəsi (AppealService çağırır) ----------
    def appeal_event(self, appeal: Appeal, event: str, **details: Any) -> None:
        handler = getattr(self, f"_on_{event}", None)
        if handler is None:
            raise ValueError(f"naməlum bildiriş hadisəsi: {event}")
        handler(appeal, **details)
        self.refresh_cards(appeal)

    # ---------- köməkçilər ----------
    def _send(self, bot: BotKind, chat_id: int, rendered: Rendered, appeal: Appeal, purpose: str) -> None:
        enqueue_rendered(self.session, bot=bot, chat_id=chat_id, rendered=rendered, appeal_id=appeal.id,
                         purpose=purpose, now=self._clock())

    def _to_staff(self, staff: Staff | None, rendered: Rendered, appeal: Appeal, purpose: str) -> None:
        if staff is not None and staff.is_active:
            self._send(BotKind.staff, staff.telegram_user_id, rendered, appeal, purpose)

    def _to_coordinators(self, rendered_for: Callable[[], Rendered], appeal: Appeal, purpose: str) -> None:
        for coordinator in recipients.coordinators(self.session):
            self._send(BotKind.staff, coordinator.telegram_user_id, rendered_for(), appeal, purpose)

    def _to_citizen(self, appeal: Appeal, rendered: Rendered, purpose: str = CITIZEN_NOTICE) -> None:
        citizen = self.session.get(Citizen, appeal.citizen_id)
        self._send(BotKind.citizen, citizen.telegram_user_id, rendered, appeal, purpose)

    def _decrypt(self, token: str | None) -> str:
        return self.crypto.decrypt(token) if token else "—"

    def _citizen_view(self, appeal: Appeal) -> CitizenView:
        c = self.session.get(Citizen, appeal.citizen_id)
        names = [c.last_name or "", c.first_name or ""]
        return CitizenView(
            full_name=" ".join(n for n in [*names, c.patronymic or ""] if n),
            short_name=" ".join(n for n in names if n),
            phone=self._decrypt(c.phone_enc),
            address=self._decrypt(c.address_enc),
            fin=self._decrypt(c.fin_enc),
        )

    def _staff_card(self, appeal: Appeal, **kwargs: Any) -> Rendered:
        return cards.staff_card(appeal, citizen=self._citizen_view(appeal), attachments=self._attachments(appeal),
                                **kwargs)

    def _attachments(self, appeal: Appeal) -> int:
        return self.session.scalar(select(func.count()).select_from(Attachment)
                                   .where(Attachment.appeal_id == appeal.id)) or 0

    def _staff(self, staff_id: int | None) -> Staff | None:
        return self.session.get(Staff, staff_id) if staff_id else None

    def _department_name(self, appeal: Appeal) -> str | None:
        department = self.session.get(Department, appeal.department_id) if appeal.department_id else None
        return department.name if department else None

    def _suggestion(self, appeal: Appeal, staff_id: int | None = None) -> Suggestion | None:
        if staff_id is None and appeal.department_id:
            found = suggest_assignee(self.session, appeal.department_id)
            staff_id = found.id if found else None
        staff = self._staff(staff_id)
        if staff is None:
            return None
        return Suggestion(staff.id, short_name(staff), open_appeal_count(self.session, staff.id))

    def _coordinator_card(self, appeal: Appeal, *, header: str | None = None,
                          suggested_staff_id: int | None = None, active: bool = True) -> Rendered:
        assignee = self._staff(appeal.assigned_staff_id)
        return cards.coordinator_card(
            appeal, citizen=self._citizen_view(appeal), department=self._department_name(appeal),
            suggestion=self._suggestion(appeal, suggested_staff_id) if active else None, header=header,
            active=active, assignee_name=assignee.full_name if assignee else None,
            attachments=self._attachments(appeal),
        )

    def _latest_answer(self, appeal: Appeal) -> AppealMessage | None:
        return self.session.scalar(
            select(AppealMessage)
            .where(AppealMessage.appeal_id == appeal.id, AppealMessage.kind == MessageKind.final_answer)
            .order_by(AppealMessage.id.desc()).limit(1)
        )

    def _approval_card(self, appeal: Appeal, *, active: bool = True) -> Rendered:
        answer = self._latest_answer(appeal)
        author = self._staff(answer.author_staff_id) if answer else None
        assignee = self._staff(appeal.assigned_staff_id)
        return cards.approval_card(
            appeal, staff_name=author.full_name if author else "—", outcome=appeal.outcome,
            reasons=(answer.meta or {}).get("approval_reasons", []) if answer else [], active=active,
            assignee_name=assignee.full_name if assignee else None,
        )

    # ---------- hadisələr ----------
    def _on_triaged(self, appeal: Appeal, suggested_staff_id: int | None = None) -> None:
        self._to_coordinators(lambda: self._coordinator_card(appeal, suggested_staff_id=suggested_staff_id),
                              appeal, COORDINATOR_CARD)

    def _on_assigned(self, appeal: Appeal, staff_id: int) -> None:
        self._to_staff(self._staff(staff_id), self._staff_card(appeal), appeal, STAFF_CARD)

    def _on_reassigned(self, appeal: Appeal, previous_staff_id: int | None, staff_id: int) -> None:
        self._on_assigned(appeal, staff_id)
        self._to_staff(self._staff(previous_staff_id),
                       cards.staff_info(f"ℹ️ {appeal.number} başqa əməkdaşa təyin olundu."), appeal, STAFF_INFO)

    def _on_returned(self, appeal: Appeal, comment: str, previous_staff_id: int | None = None) -> None:
        who = self._staff(previous_staff_id)
        header = f"↩️ İcraçı qaytardı ({who.full_name if who else '—'}): {comment}"
        self._to_coordinators(lambda: self._coordinator_card(appeal, header=header), appeal, COORDINATOR_CARD)

    def _on_released(self, appeal: Appeal, **_: Any) -> None:
        header = "♻️ İcraçı deaktiv edilib: müraciəti yenidən təyin edin"
        self._to_coordinators(lambda: self._coordinator_card(appeal, header=header), appeal, COORDINATOR_CARD)

    def _on_question_to_citizen(self, appeal: Appeal, message_id: int, question: str) -> None:
        self._to_citizen(appeal, cards.citizen_question(appeal, question=question, templates=self.config.templates),
                         CITIZEN_QUESTION)

    def _on_citizen_replied(self, appeal: Appeal, message_id: int) -> None:
        rendered = cards.citizen_message_for_staff(appeal, self.session.get(AppealMessage, message_id).text)
        assignee = self._staff(appeal.assigned_staff_id)
        if assignee is not None and assignee.is_active:
            self._to_staff(assignee, rendered, appeal, STAFF_INFO)
        else:  # icraçı yoxdur: koordinatorlar görsün
            self._to_coordinators(lambda: rendered, appeal, STAFF_INFO)

    def _on_answer_sent(self, appeal: Appeal, message_id: int | None) -> None:
        answer = self.session.get(AppealMessage, message_id) if message_id else self._latest_answer(appeal)
        for part in cards.citizen_answer(appeal, answer=answer.text, outcome=appeal.outcome,
                                         templates=self.config.templates, org_name=self.org_name):
            self._to_citizen(appeal, part, CITIZEN_ANSWER)

    def _on_approval_requested(self, appeal: Appeal, message_id: int, reasons: list[str]) -> None:
        text = self.session.get(AppealMessage, message_id).text
        answer_text = cards.staff_info(f"📝 {appeal.number}: təsdiq üçün cavab mətni:\n\n{text}")
        for coordinator in recipients.coordinators(self.session):
            self._send(BotKind.staff, coordinator.telegram_user_id, answer_text, appeal, ANSWER_TEXT)
            self._send(BotKind.staff, coordinator.telegram_user_id, self._approval_card(appeal), appeal,
                       APPROVAL_CARD)

    def _on_sent_back(self, appeal: Appeal, comment: str) -> None:
        card = self._staff_card(appeal, header=f"↩️ Koordinator cavabı geri qaytardı: {comment}")
        self._to_staff(self._staff(appeal.assigned_staff_id), card, appeal, STAFF_CARD)

    def _on_redirected(self, appeal: Appeal, target: str, letter_ref: str | None = None) -> None:
        sent_date = self._clock().astimezone(cards.BAKU).date()
        self._to_citizen(appeal, cards.citizen_redirected(appeal, target=target, letter_ref=letter_ref,
                                                          sent_date=sent_date, templates=self.config.templates))

    def _on_not_reviewed(self, appeal: Appeal, reason: str) -> None:
        self._to_citizen(appeal, cards.citizen_not_reviewed(appeal, reason=reason, templates=self.config.templates))

    def _on_extended(self, appeal: Appeal, due_date, reason: str) -> None:
        self._to_citizen(appeal, cards.citizen_extended(appeal, due_date=due_date, reason=reason,
                                                        templates=self.config.templates))

    def _on_reopened(self, appeal: Appeal, reason: str | None = None) -> None:
        self._to_citizen(appeal, cards.citizen_reopened(appeal, templates=self.config.templates))
        header = f"🔁 Vətəndaş qane olmadı: {reason or 'səbəb göstərilməyib'}"
        self._to_coordinators(lambda: self._coordinator_card(appeal, header=header), appeal, COORDINATOR_CARD)

    # ---------- əmrlər (/tap, /islerim): kartı tələb edən əməkdaşa ----------
    def show_card(self, appeal: Appeal, viewer: Staff) -> None:
        """Müraciətin cari vəziyyətinə uyğun kart. Aktiv kartlar qeydə alınır ki, sonra yenilənsin."""
        coordinator = viewer.role == StaffRole.coordinator
        if appeal.status == AppealStatus.new and coordinator:
            self._to_staff(viewer, self._coordinator_card(appeal), appeal, COORDINATOR_CARD)
        elif appeal.status == AppealStatus.pending_approval and coordinator:
            answer = self._latest_answer(appeal)
            if answer:
                text = f"📝 {appeal.number}: təsdiq üçün cavab mətni:\n\n{answer.text}"
                self._to_staff(viewer, cards.staff_info(text), appeal, ANSWER_TEXT)
            self._to_staff(viewer, self._approval_card(appeal), appeal, APPROVAL_CARD)
        elif appeal.status in STAFF_ACTIVE and (coordinator or appeal.assigned_staff_id == viewer.id):
            self._to_staff(viewer, self._staff_card(appeal), appeal, STAFF_CARD)
        else:
            assignee = self._staff(appeal.assigned_staff_id)
            self._to_staff(viewer, cards.coordinator_card(
                appeal, citizen=self._citizen_view(appeal), department=self._department_name(appeal),
                suggestion=None, active=False, assignee_name=assignee.full_name if assignee else None,
                attachments=self._attachments(appeal)), appeal, STAFF_INFO)

    # ---------- kartların yenilənməsi ----------
    def refresh_cards(self, appeal: Appeal) -> None:
        rows = self.session.scalars(
            select(TelegramMessage).where(
                TelegramMessage.appeal_id == appeal.id,
                TelegramMessage.bot == BotKind.staff,
                TelegramMessage.purpose.in_(CARD_PURPOSES),
                TelegramMessage.closed_at.is_(None),
            ).order_by(TelegramMessage.id)
        ).all()
        for row in rows:
            rendered, active = self._render_existing_card(appeal, row)
            enqueue_edit(self.session, bot=BotKind.staff, chat_id=row.chat_id, message_id=row.message_id,
                         rendered=rendered, appeal_id=appeal.id, purpose=row.purpose, now=self._clock())
            if not active:
                row.closed_at = self._clock()

    def _render_existing_card(self, appeal: Appeal, row: TelegramMessage) -> tuple[Rendered, bool]:
        if row.purpose == COORDINATOR_CARD:
            active = appeal.status == AppealStatus.new
            return self._coordinator_card(appeal, active=active), active
        if row.purpose == APPROVAL_CARD:
            active = appeal.status == AppealStatus.pending_approval
            return self._approval_card(appeal, active=active), active
        owner = self.session.scalar(select(Staff).where(Staff.telegram_user_id == row.chat_id))
        active = appeal.status in STAFF_ACTIVE and owner is not None and appeal.assigned_staff_id == owner.id
        assignee = self._staff(appeal.assigned_staff_id)
        return self._staff_card(appeal, active=active,
                                assignee_name=assignee.full_name if assignee else None), active
