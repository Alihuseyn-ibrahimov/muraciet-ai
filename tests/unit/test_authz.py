from types import SimpleNamespace

import pytest

from app.workflow.authz import Actor, authorize, require_admin
from app.workflow.errors import Forbidden
from app.workflow.machine import Action as A
from app.workflow.states import StaffRole

pytestmark = pytest.mark.unit

COORDINATOR, STAFF, OTHER_STAFF, HEAD = 1, 2, 3, 4
CITIZEN, OTHER_CITIZEN = 10, 11

# Hər ssenari: (icraçı, müraciət). Müraciət ya 2 nömrəli əməkdaşa, ya da koordinatorun özünə təyin olunub.
SCENARIOS = {
    "coordinator": (Actor.staff(COORDINATOR, StaffRole.coordinator), STAFF),
    "coordinator_assignee": (Actor.staff(COORDINATOR, StaffRole.coordinator), COORDINATOR),
    "staff_assignee": (Actor.staff(STAFF, StaffRole.staff), STAFF),
    "staff_other": (Actor.staff(OTHER_STAFF, StaffRole.staff), STAFF),
    "head": (Actor.staff(HEAD, StaffRole.head), STAFF),
    "inactive_assignee": (Actor.staff(STAFF, StaffRole.staff, is_active=False), STAFF),
    "citizen_owner": (Actor.citizen(CITIZEN), STAFF),
    "citizen_other": (Actor.citizen(OTHER_CITIZEN), STAFF),
    "system": (Actor.system(), STAFF),
}

COORD = {"coordinator", "coordinator_assignee"}
ASSIGNEE = {"coordinator_assignee", "staff_assignee"}
# Spec 7-ci bölmədəki rollar cədvəli. Burada olmayan hər şey qadağandır.
ALLOWED = {
    A.apply_triage: {"system"},
    A.auto_close: {"system"},
    A.release: {"system"},
    A.assign: COORD,
    A.reassign: COORD,
    A.redirect: COORD,
    A.not_review: COORD,
    A.extend: COORD,
    A.approve: COORD,
    A.send_back: COORD,
    A.send_answer: COORD | ASSIGNEE,
    A.reveal_fin: COORD | ASSIGNEE,
    A.view_attachments: COORD | ASSIGNEE,
    A.ask_citizen: ASSIGNEE,
    A.return_to_coordinator: ASSIGNEE,
    A.request_approval: ASSIGNEE,
    A.citizen_reply: {"citizen_owner"},
    A.feedback_positive: {"citizen_owner"},
    A.feedback_negative: {"citizen_owner"},
    A.create: set(),  # müraciəti servis vətəndaşın şəxsiyyətini yoxlayaraq yaradır, authz bunu etmir
}


def test_matrix_covers_every_action():
    assert set(ALLOWED) == set(A)


@pytest.mark.parametrize("label", list(SCENARIOS))
@pytest.mark.parametrize("action", list(A))
def test_authorization_matrix(label, action):
    actor, assignee = SCENARIOS[label]
    appeal = SimpleNamespace(citizen_id=CITIZEN, assigned_staff_id=assignee)
    if label in ALLOWED[action]:
        authorize(actor, action, appeal)
    else:
        with pytest.raises(Forbidden):
            authorize(actor, action, appeal)


def test_forbidden_message_is_human_readable():
    actor, _ = SCENARIOS["staff_other"]
    with pytest.raises(Forbidden, match="sizə təyin olunmayıb"):
        authorize(actor, A.send_answer, SimpleNamespace(citizen_id=CITIZEN, assigned_staff_id=STAFF))


def test_inactive_staff_message():
    actor, _ = SCENARIOS["inactive_assignee"]
    with pytest.raises(Forbidden, match="deaktiv"):
        authorize(actor, A.send_answer, SimpleNamespace(citizen_id=CITIZEN, assigned_staff_id=STAFF))


def test_require_admin():
    require_admin(Actor.staff(COORDINATOR, StaffRole.coordinator, is_admin=True))
    for actor in (Actor.staff(COORDINATOR, StaffRole.coordinator), Actor.citizen(CITIZEN), Actor.system(),
                  Actor.staff(COORDINATOR, StaffRole.coordinator, is_admin=True, is_active=False)):
        with pytest.raises(Forbidden):
            require_admin(actor)
