"""Kim nə edə bilər (spec 7-ci və 9-cu bölmələr). Bütün icazələr yalnız burada yoxlanılır.

İcazə üç şeydən asılıdır: icraçının növü və rolu, onun aktiv olub-olmaması və müraciətin kimə aid
olması (vətəndaşın öz müraciəti, əməkdaşın özünə təyin olunmuş müraciət). Müraciətin vəziyyətini
isə machine.py yoxlayır.
"""
from dataclasses import dataclass
from typing import Protocol

from app.workflow.errors import Forbidden
from app.workflow.machine import ACTION_LABELS, Action
from app.workflow.states import ActorType, StaffRole

A = Action
SYSTEM_ACTIONS = frozenset({A.apply_triage, A.auto_close, A.release})
CITIZEN_ACTIONS = frozenset({A.citizen_reply, A.feedback_positive, A.feedback_negative})
COORDINATOR_ACTIONS = frozenset(
    {A.assign, A.reassign, A.redirect, A.not_review, A.extend, A.approve, A.send_back, A.send_answer, A.reveal_fin,
     A.view_attachments}
)
ASSIGNEE_ACTIONS = frozenset({A.ask_citizen, A.return_to_coordinator, A.send_answer, A.request_approval, A.reveal_fin,
                              A.view_attachments})


class AppealRef(Protocol):
    citizen_id: int
    assigned_staff_id: int | None


@dataclass(frozen=True)
class Actor:
    type: ActorType
    id: int | None = None
    role: StaffRole | None = None
    is_admin: bool = False
    is_active: bool = True

    @classmethod
    def staff(cls, staff_id: int, role: StaffRole, *, is_admin: bool = False, is_active: bool = True) -> "Actor":
        return cls(ActorType.staff, staff_id, role, is_admin, is_active)

    @classmethod
    def citizen(cls, citizen_id: int) -> "Actor":
        return cls(ActorType.citizen, citizen_id)

    @classmethod
    def system(cls) -> "Actor":
        return cls(ActorType.system)

    @property
    def is_coordinator(self) -> bool:
        return self.type == ActorType.staff and self.role == StaffRole.coordinator


def authorize(actor: Actor, action: Action, appeal: AppealRef) -> None:
    label = ACTION_LABELS[action]
    if actor.type == ActorType.system:
        if action in SYSTEM_ACTIONS:
            return
        raise Forbidden(f"'{label}' əməliyyatını sistem edə bilməz")

    if actor.type == ActorType.citizen:
        if action in CITIZEN_ACTIONS and appeal.citizen_id == actor.id:
            return
        raise Forbidden("Bu müraciət sizə aid deyil" if action in CITIZEN_ACTIONS else f"'{label}' vətəndaş üçün deyil")

    if not actor.is_active:
        raise Forbidden("Hesabınız deaktiv edilib")
    if actor.role == StaffRole.coordinator and action in COORDINATOR_ACTIONS:
        return
    if action in ASSIGNEE_ACTIONS:
        is_assignee = appeal.assigned_staff_id == actor.id and actor.role in (StaffRole.staff, StaffRole.coordinator)
        if is_assignee:
            return
        raise Forbidden("Bu müraciət sizə təyin olunmayıb")
    raise Forbidden(f"'{label}' üçün icazəniz yoxdur")


def require_admin(actor: Actor) -> None:
    if actor.type == ActorType.staff and actor.is_active and actor.is_admin:
        return
    raise Forbidden("Bu əməliyyat yalnız admin üçündür")
