"""Müraciət sisteminin sabit dəyərləri (spec 7-ci və 8-ci bölmələr). Yeganə mənbə budur:
DB enum-ları, konfiqurasiyanın validasiyası və workflow eyni dəyərləri istifadə edir."""
from enum import StrEnum


class Category(StrEnum):
    complaint = "complaint"
    request = "request"
    suggestion = "suggestion"
    information = "information"


class Priority(StrEnum):
    normal = "normal"
    high = "high"
    urgent = "urgent"  # m. 10.4: 24 saat


class Outcome(StrEnum):
    satisfied = "satisfied"
    partial = "partial"
    rejected = "rejected"
    explained = "explained"


class AppealStatus(StrEnum):
    new = "new"
    assigned = "assigned"
    waiting_citizen = "waiting_citizen"
    pending_approval = "pending_approval"
    answered = "answered"
    closed = "closed"
    redirected = "redirected"
    not_reviewed = "not_reviewed"


class StaffRole(StrEnum):
    coordinator = "coordinator"
    staff = "staff"
    head = "head"


class MessageKind(StrEnum):
    staff_question = "staff_question"
    citizen_reply = "citizen_reply"
    ai_draft = "ai_draft"
    qa_report = "qa_report"
    final_answer = "final_answer"
    coordinator_comment = "coordinator_comment"


class ActorType(StrEnum):
    citizen = "citizen"
    staff = "staff"
    system = "system"
    ai = "ai"


class BotKind(StrEnum):
    citizen = "citizen"
    staff = "staff"


class OutboxStatus(StrEnum):
    pending = "pending"
    sending = "sending"
    sent = "sent"
    dead = "dead"


class JobStatus(StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    dead = "dead"


FINAL_STATUSES = frozenset({AppealStatus.closed, AppealStatus.redirected, AppealStatus.not_reviewed})
OPEN_STATUSES = frozenset(
    {AppealStatus.new, AppealStatus.assigned, AppealStatus.waiting_citizen, AppealStatus.pending_approval}
)

CATEGORY_LABELS = {
    Category.complaint: "Şikayət",
    Category.request: "Ərizə",
    Category.suggestion: "Təklif",
    Category.information: "Məlumat sorğusu",
}
PRIORITY_LABELS = {Priority.normal: "Normal", Priority.high: "Yüksək", Priority.urgent: "Təcili"}
OUTCOME_LABELS = {
    Outcome.satisfied: "Təmin edildi",
    Outcome.partial: "Qismən təmin edildi",
    Outcome.rejected: "Rədd edildi",
    Outcome.explained: "İzahat verildi",
}
# Əməkdaşlar üçün: daxili vəziyyət dəqiq göstərilir
STAFF_STATUS_LABELS = {
    AppealStatus.new: "Yeni",
    AppealStatus.assigned: "İcrada",
    AppealStatus.waiting_citizen: "Vətəndaşın cavabı gözlənilir",
    AppealStatus.pending_approval: "Təsdiq gözləyir",
    AppealStatus.answered: "Cavablandırılıb",
    AppealStatus.closed: "Bağlanıb",
    AppealStatus.redirected: "Aidiyyəti üzrə göndərilib",
    AppealStatus.not_reviewed: "Baxılmamış saxlanılıb",
}
# Vətəndaş üçün
STATUS_LABELS = {
    AppealStatus.new: "Qeydə alınıb",
    AppealStatus.assigned: "İcradadır",
    AppealStatus.waiting_citizen: "Sizin cavabınız gözlənilir",
    AppealStatus.pending_approval: "İcradadır",  # vətəndaş üçün daxili təsdiq ayrıca göstərilmir
    AppealStatus.answered: "Cavablandırılıb",
    AppealStatus.closed: "Bağlanıb",
    AppealStatus.redirected: "Aidiyyəti üzrə göndərilib",
    AppealStatus.not_reviewed: "Baxılmamış saxlanılıb",
}
