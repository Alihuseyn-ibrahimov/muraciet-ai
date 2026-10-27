from types import SimpleNamespace

import pytest

from app.workflow.approval import needs_approval
from app.workflow.authz import Actor
from app.workflow.states import Category, Outcome, Priority, StaffRole
from app.yaml_config import ApprovalRules, load_config

pytestmark = pytest.mark.unit

STAFF = Actor.staff(2, StaffRole.staff)
COORDINATOR = Actor.staff(1, StaffRole.coordinator)
RULES = load_config().approval


def appeal(**fields):
    base = dict(category=Category.request, priority=Priority.normal, legal_issue=False, triage_failed=False)
    return SimpleNamespace(**(base | fields))


def decide(*, actor=STAFF, rules=RULES, outcome=Outcome.satisfied, qa_critical=False, **fields):
    return needs_approval(appeal(**fields), outcome=outcome, qa_critical=qa_critical, actor=actor, rules=rules)


def test_ordinary_answer_goes_directly():
    d = decide()
    assert not d.required and d.reasons == []


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"category": Category.complaint}, "Şikayət"),
        ({"priority": Priority.high}, "Yüksək"),
        ({"priority": Priority.urgent}, "Təcili"),
        ({"legal_issue": True}, "Hüquqi"),
        ({"triage_failed": True}, "AI"),
    ],
)
def test_appeal_based_rules(fields, reason):
    d = decide(**fields)
    assert d.required and any(reason in r for r in d.reasons)


@pytest.mark.parametrize("outcome", [Outcome.rejected, Outcome.partial])
def test_rejected_and_partial_need_approval(outcome):
    assert decide(outcome=outcome).required


def test_critical_qa_issue_needs_approval():
    d = decide(qa_critical=True)
    assert d.required and any("kritik" in r for r in d.reasons)


def test_missing_qa_check_needs_approval():
    # Keyfiyyət agenti xəta veribsə (None), təhlükəsiz standart: insan təsdiqi
    d = decide(qa_critical=None)
    assert d.required and any("aparılmadı" in r for r in d.reasons)


def test_all_reasons_are_listed():
    d = decide(category=Category.complaint, outcome=Outcome.rejected, qa_critical=True)
    assert len(d.reasons) == 3


def test_coordinator_own_answer_never_needs_approval():
    assert not decide(actor=COORDINATOR, category=Category.complaint, outcome=Outcome.rejected, qa_critical=True).required


def test_rules_can_be_disabled_in_config():
    relaxed = ApprovalRules(categories=[], priorities=[], outcomes=[], legal_issue=False,
                            qa_critical=False, agent_failure=False)
    assert not decide(rules=relaxed, category=Category.complaint, legal_issue=True, qa_critical=None).required
