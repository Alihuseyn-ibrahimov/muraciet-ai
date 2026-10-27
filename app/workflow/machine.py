"""Müraciətin state machine-i (spec 7-ci bölmə, dəqiqləşdirmələr findings.md-də).

Hansı vəziyyətdə hansı əməliyyatın mümkün olduğu yalnız burada müəyyən olunur. Kimin edə
biləcəyini authz.py, əlavə nəticələri (bildiriş, audit, iş) service.py həll edir.
"""
from enum import StrEnum

from app.workflow.errors import InvalidTransition
from app.workflow.states import STAFF_STATUS_LABELS, AppealStatus as S


class Action(StrEnum):
    create = "create"
    apply_triage = "apply_triage"
    assign = "assign"
    reassign = "reassign"
    redirect = "redirect"
    not_review = "not_review"
    extend = "extend"
    ask_citizen = "ask_citizen"
    citizen_reply = "citizen_reply"
    return_to_coordinator = "return_to_coordinator"
    send_answer = "send_answer"
    request_approval = "request_approval"
    approve = "approve"
    send_back = "send_back"
    feedback_positive = "feedback_positive"
    feedback_negative = "feedback_negative"
    auto_close = "auto_close"
    release = "release"  # əməkdaş deaktiv ediləndə
    reveal_fin = "reveal_fin"  # vəziyyəti dəyişmir, yalnız icazə və audit
    view_attachments = "view_attachments"  # vəziyyəti dəyişmir, yalnız icazə və audit


ACTION_LABELS = {
    Action.create: "Müraciət yaratmaq",
    Action.apply_triage: "Triaj",
    Action.assign: "Təyin etmək",
    Action.reassign: "Yenidən təyin etmək",
    Action.redirect: "Aidiyyəti üzrə göndərmək",
    Action.not_review: "Baxılmamış saxlamaq",
    Action.extend: "Müddəti uzatmaq",
    Action.ask_citizen: "Vətəndaşa sual vermək",
    Action.citizen_reply: "Vətəndaşın cavabı",
    Action.return_to_coordinator: "Koordinatora qaytarmaq",
    Action.send_answer: "Cavab göndərmək",
    Action.request_approval: "Təsdiqə göndərmək",
    Action.approve: "Təsdiqləmək",
    Action.send_back: "Geri qaytarmaq",
    Action.feedback_positive: "Qane oldum",
    Action.feedback_negative: "Qane olmadım",
    Action.auto_close: "Avtomatik bağlama",
    Action.release: "İcraçını azad etmək",
    Action.reveal_fin: "FİN-ə baxmaq",
    Action.view_attachments: "Əlavələrə baxmaq",
}

A = Action
TRANSITIONS: dict[tuple[S, Action], S] = {
    (S.new, A.apply_triage): S.new,
    (S.new, A.assign): S.assigned,
    (S.new, A.redirect): S.redirected,
    (S.new, A.not_review): S.not_reviewed,
    (S.new, A.extend): S.new,
    (S.new, A.citizen_reply): S.new,

    (S.assigned, A.ask_citizen): S.waiting_citizen,
    (S.assigned, A.return_to_coordinator): S.new,
    (S.assigned, A.send_answer): S.answered,
    (S.assigned, A.request_approval): S.pending_approval,
    (S.assigned, A.reassign): S.assigned,
    (S.assigned, A.extend): S.assigned,
    (S.assigned, A.citizen_reply): S.assigned,
    (S.assigned, A.release): S.new,

    (S.waiting_citizen, A.citizen_reply): S.assigned,
    (S.waiting_citizen, A.ask_citizen): S.waiting_citizen,
    (S.waiting_citizen, A.return_to_coordinator): S.new,
    (S.waiting_citizen, A.send_answer): S.answered,
    (S.waiting_citizen, A.request_approval): S.pending_approval,
    (S.waiting_citizen, A.reassign): S.waiting_citizen,
    (S.waiting_citizen, A.extend): S.waiting_citizen,
    (S.waiting_citizen, A.release): S.new,

    (S.pending_approval, A.approve): S.answered,
    (S.pending_approval, A.send_back): S.assigned,
    (S.pending_approval, A.extend): S.pending_approval,
    (S.pending_approval, A.citizen_reply): S.pending_approval,

    (S.answered, A.feedback_positive): S.closed,
    (S.answered, A.feedback_negative): S.new,
    (S.answered, A.auto_close): S.closed,
}


def next_status(status: S, action: Action) -> S:
    try:
        return TRANSITIONS[(status, action)]
    except KeyError:
        raise InvalidTransition(
            f"'{ACTION_LABELS[action]}' əməliyyatı '{STAFF_STATUS_LABELS[status]}' vəziyyətində mümkün deyil"
        ) from None
