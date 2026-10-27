from itertools import product

import pytest

from app.workflow.errors import InvalidTransition
from app.workflow.machine import TRANSITIONS, Action, next_status
from app.workflow.states import FINAL_STATUSES, AppealStatus as S

pytestmark = pytest.mark.unit

A = Action
# Spec 7-ci bölmə + findings.md-dəki dəqiqləşdirmələr. Bu cədvəl implementasiyadan asılı olmadan yazılıb.
EXPECTED = {
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


def test_transition_table_matches_spec_exactly():
    assert TRANSITIONS == EXPECTED


@pytest.mark.parametrize(("start", "action"), list(EXPECTED))
def test_allowed_transition(start, action):
    assert next_status(start, action) == EXPECTED[(start, action)]


@pytest.mark.parametrize(("start", "action"), [p for p in product(S, A) if p not in EXPECTED])
def test_every_other_pair_is_forbidden(start, action):
    with pytest.raises(InvalidTransition):
        next_status(start, action)


@pytest.mark.parametrize("final", sorted(FINAL_STATUSES))
def test_final_statuses_have_no_way_out(final):
    assert not [a for (s, a) in TRANSITIONS if s == final]


def test_invalid_transition_message_is_human_readable():
    with pytest.raises(InvalidTransition, match="Bağlanıb"):
        next_status(S.closed, A.assign)
