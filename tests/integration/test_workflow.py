"""AppealService: müraciətin bütün həyat dövrü real PostgreSQL-də (LLM və Telegram olmadan)."""
from datetime import date, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.tables import Appeal, AppealEvent, AppealMessage, Department, Job
from app.workflow.authz import Actor
from app.workflow.errors import Forbidden, InvalidTransition, StaleAppeal, ValidationFailed
from app.workflow.service import AppealService, RoutingOutcome, TriageOutcome
from app.workflow.states import AppealStatus as S
from app.workflow.states import Category, MessageKind, Outcome, Priority
from app.workflow.suggest import suggest_assignee
from tests.fakes import RecordingNotifier
from tests.integration.helpers import CAL, CFG, CRYPTO, MONDAY_10, Clock, World, complete_citizen

pytestmark = pytest.mark.integration

def run(app_engine, fn, clock: Clock | None = None):
    """fn(service)-i ayrıca tranzaksiyada işə salır: (nəticə, bildirişlər)."""
    notifier = RecordingNotifier()
    with Session(app_engine, expire_on_commit=False) as s, s.begin():
        service = AppealService(s, config=CFG, calendar=CAL, crypto=CRYPTO, notifier=notifier,
                                clock=clock or Clock(MONDAY_10))
        result = fn(service)
    return result, notifier


def new_appeal(app_engine, world: World, *, category=Category.request, priority=Priority.normal,
               legal_issue=False, citizen_id=None) -> Appeal:
    """Qeydə alınmış və triaj olunmuş müraciət (status: new)."""
    appeal, _ = run(app_engine, lambda svc: svc.create_appeal(
        citizen_id or world.citizen_id, subject="Küçə işıqlandırması", summary="3 gündür işıqlar yanmır",
        description="Nizami küçəsi 5 yaxınlığında 3 gündür işıqlar yanmır"))
    appeal, _ = run(app_engine, lambda svc: svc.apply_triage(
        appeal.id, TriageOutcome(category, priority, legal_issue), RoutingOutcome("pensiya", "pensiya ödənişi")))
    return appeal


def assigned_appeal(app_engine, world: World, staff: Actor | None = None, **kw) -> Appeal:
    appeal = new_appeal(app_engine, world, **kw)
    appeal, _ = run(app_engine, lambda svc: svc.assign(world.coordinator, appeal.id, (staff or world.staff_a).id,
                                                         expected_version=appeal.version))
    return appeal


def events(app_engine, appeal_id: int) -> list[str]:
    with Session(app_engine) as s:
        return list(s.scalars(select(AppealEvent.action).where(AppealEvent.appeal_id == appeal_id)
                              .order_by(AppealEvent.id)))


# ---------- qeydiyyat və triaj ----------
def test_create_appeal_assigns_number_due_date_and_triage_job(app_engine, world):
    appeal, _ = run(app_engine, lambda svc: svc.create_appeal(
        world.citizen_id, subject="Küçə işığı", summary="Yanmır", description="3 gündür yanmır"))
    assert appeal.number == f"MR-2026-{appeal.id:06d}"
    assert appeal.status == S.new and appeal.version == 1
    assert appeal.due_date == appeal.original_due_date == date(2026, 11, 6)  # 15 iş günü (m. 10.1)
    with Session(app_engine) as s:
        assert s.scalar(select(Job.kind).where(Job.appeal_id == appeal.id)) == "triage"
    assert events(app_engine, appeal.id) == ["create"]


def test_anonymous_appeal_is_rejected(app_engine, world):
    with Session(app_engine, expire_on_commit=False) as s, s.begin():
        incomplete = complete_citizen(5009, fin_enc=None, fin_hash=None, patronymic=None)
        s.add(incomplete)
        s.flush()
    with pytest.raises(ValidationFailed, match="FİN") as exc:
        run(app_engine, lambda svc: svc.create_appeal(incomplete.id, subject="x", summary="y", description="z"))
    assert "ata adı" in str(exc.value)


def test_triage_suggests_least_loaded_staff(app_engine, world):
    assigned_appeal(app_engine, world, world.staff_a)  # A-nın bir açıq işi var
    appeal, _ = run(app_engine, lambda svc: svc.create_appeal(
        world.citizen_id, subject="Su", summary="Su yoxdur", description="2 gündür su yoxdur"))
    _, notes = run(app_engine, lambda svc: svc.apply_triage(
        appeal.id, TriageOutcome(Category.complaint, Priority.normal, False), RoutingOutcome("pensiya", "su")))
    assert notes.last("triaged")["suggested_staff_id"] == world.staff_b.id


def test_triage_failure_falls_back_safely(app_engine, world):
    appeal, _ = run(app_engine, lambda svc: svc.create_appeal(
        world.citizen_id, subject="x", summary="y", description="z"))
    appeal, _ = run(app_engine, lambda svc: svc.apply_triage(appeal.id, None, None))
    with Session(app_engine) as s:
        general_id = s.scalar(select(Department.id).where(Department.code == "general"))
    assert (appeal.category, appeal.priority, appeal.triage_failed) == (Category.request, Priority.normal, True)
    assert appeal.department_id == general_id


def test_unknown_department_code_goes_to_general(app_engine, world):
    appeal, _ = run(app_engine, lambda svc: svc.create_appeal(
        world.citizen_id, subject="x", summary="y", description="z"))
    appeal, _ = run(app_engine, lambda svc: svc.apply_triage(
        appeal.id, TriageOutcome(Category.request, Priority.normal, False), RoutingOutcome("uydurma", "?")))
    with Session(app_engine) as s:
        assert s.get(Department, appeal.department_id).code == "general"


def test_urgent_priority_sets_24_hour_deadline(app_engine, world):
    appeal = new_appeal(app_engine, world, priority=Priority.urgent)
    assert appeal.urgent_deadline == MONDAY_10 + timedelta(hours=24)  # m. 10.4


def test_triage_is_idempotent(app_engine, world):
    appeal = new_appeal(app_engine, world)
    _, notes = run(app_engine, lambda svc: svc.apply_triage(appeal.id, None, None))
    assert notes.names() == []
    assert events(app_engine, appeal.id).count("apply_triage") == 1


# ---------- əsas dövrə ----------
def test_full_happy_path(app_engine, world):
    clock = Clock(MONDAY_10)
    appeal = new_appeal(app_engine, world)
    clock.moment = MONDAY_10 + timedelta(hours=2)
    appeal, notes = run(app_engine, lambda svc: svc.assign(world.coordinator, appeal.id, world.staff_a.id,
                                                           expected_version=appeal.version), clock)
    assert appeal.status == S.assigned and float(appeal.assign_work_hours) == 2.0
    assert notes.names() == ["assigned"]

    appeal, notes = run(app_engine, lambda svc: svc.ask_citizen(
        world.staff_a, appeal.id, "Hansı binanın yanındadır?", expected_version=appeal.version), clock)
    assert appeal.status == S.waiting_citizen and notes.names() == ["question_to_citizen"]

    appeal, notes = run(app_engine, lambda svc: svc.citizen_reply(world.citizen_id, appeal.id, "5 nömrəli bina"))
    assert appeal.status == S.assigned and notes.names() == ["citizen_replied"]

    clock.moment = MONDAY_10 + timedelta(days=1)  # çərşənbə axşamı 10:00 → 9 iş saatı
    appeal, notes = run(app_engine, lambda svc: svc.submit_answer(
        world.staff_a, appeal.id, outcome=Outcome.satisfied, text="İşıqlar bərpa olundu.", qa_critical=False,
        expected_version=appeal.version), clock)
    assert appeal.status == S.answered and notes.names() == ["answer_sent"]
    assert appeal.answered_at == clock.moment and float(appeal.answer_work_hours) == 9.0

    appeal, _ = run(app_engine, lambda svc: svc.citizen_feedback(world.citizen_id, appeal.id, satisfied=True))
    assert appeal.status == S.closed and appeal.satisfaction == 1
    assert events(app_engine, appeal.id) == [
        "create", "apply_triage", "assign", "ask_citizen", "citizen_reply", "send_answer", "feedback_positive",
    ]


def test_complaint_goes_through_approval(app_engine, world):
    appeal = assigned_appeal(app_engine, world, category=Category.complaint)
    appeal, notes = run(app_engine, lambda svc: svc.submit_answer(
        world.staff_a, appeal.id, outcome=Outcome.satisfied, text="Cavab", qa_critical=False,
        expected_version=appeal.version))
    assert appeal.status == S.pending_approval and appeal.needs_approval
    assert any("Şikayət" in r for r in notes.last("approval_requested")["reasons"])

    appeal, notes = run(app_engine, lambda svc: svc.approve(world.coordinator, appeal.id,
                                                            expected_version=appeal.version))
    assert appeal.status == S.answered and notes.names() == ["answer_sent"]


def test_send_back_returns_to_assignee_with_comment(app_engine, world):
    appeal = assigned_appeal(app_engine, world, category=Category.complaint)
    appeal, _ = run(app_engine, lambda svc: svc.submit_answer(
        world.staff_a, appeal.id, outcome=Outcome.satisfied, text="Cavab", qa_critical=False,
        expected_version=appeal.version))
    appeal, notes = run(app_engine, lambda svc: svc.send_back(
        world.coordinator, appeal.id, "Tədbirləri yazın (m. 7.7)", expected_version=appeal.version))
    assert appeal.status == S.assigned and notes.last("sent_back")["comment"] == "Tədbirləri yazın (m. 7.7)"


def test_coordinator_own_answer_skips_approval(app_engine, world):
    appeal = assigned_appeal(app_engine, world, category=Category.complaint)
    appeal, _ = run(app_engine, lambda svc: svc.submit_answer(
        world.coordinator, appeal.id, outcome=Outcome.rejected, text="Cavab", qa_critical=True,
        expected_version=appeal.version))
    assert appeal.status == S.answered



@pytest.mark.parametrize("text", ["Səbəbi [...] olub.", "Tarix: [ … ]", "Ödəniş [. . .] bərpa olunacaq"])
def test_answer_with_unfilled_placeholder_is_never_sent(app_engine, world, text):
    # Real yoxlamada tapıldı: AI layihəsi "[...]" boşluqları ilə vətəndaşa getmişdi (koordinator cavabı, təsdiqsiz)
    appeal = assigned_appeal(app_engine, world)
    with pytest.raises(ValidationFailed, match=r"\[\.\.\.\]"):
        run(app_engine, lambda svc: svc.submit_answer(world.coordinator, appeal.id, outcome=Outcome.satisfied,
                                                      text=text, qa_critical=False, expected_version=appeal.version))

# ---------- eyni vaxtda əməliyyatlar və icazələr ----------
def test_stale_button_is_rejected_with_current_state(app_engine, world):
    appeal = new_appeal(app_engine, world)
    run(app_engine, lambda svc: svc.assign(world.coordinator, appeal.id, world.staff_a.id,
                                           expected_version=appeal.version))
    with pytest.raises(StaleAppeal) as exc:  # əvəzedicinin kartında köhnə versiya qalıb
        run(app_engine, lambda svc: svc.assign(world.deputy, appeal.id, world.staff_b.id,
                                               expected_version=appeal.version))
    assert exc.value.current["assigned_staff_id"] == world.staff_a.id
    assert exc.value.current["status"] == S.assigned


def test_appeal_row_is_locked_while_another_operation_runs(app_engine, world):
    appeal = new_appeal(app_engine, world)
    with Session(app_engine) as first, first.begin():
        AppealService(first, config=CFG, calendar=CAL, crypto=CRYPTO, notifier=RecordingNotifier(),
                      clock=Clock(MONDAY_10)).assign(world.coordinator, appeal.id, world.staff_a.id,
                                                     expected_version=appeal.version)
        with Session(app_engine) as second, second.begin():  # birincinin tranzaksiyası hələ açıqdır
            second.execute(text("SET LOCAL lock_timeout = '300ms'"))
            service = AppealService(second, config=CFG, calendar=CAL, crypto=CRYPTO,
                                    notifier=RecordingNotifier(), clock=Clock(MONDAY_10))
            with pytest.raises(OperationalError, match="lock timeout"):
                service.assign(world.deputy, appeal.id, world.staff_b.id, expected_version=appeal.version)


def test_other_staff_and_head_are_forbidden(app_engine, world):
    appeal = assigned_appeal(app_engine, world)
    with pytest.raises(Forbidden):
        run(app_engine, lambda svc: svc.submit_answer(world.staff_b, appeal.id, outcome=Outcome.satisfied,
                                                      text="x", qa_critical=False, expected_version=appeal.version))
    with pytest.raises(Forbidden):
        run(app_engine, lambda svc: svc.reassign(world.head, appeal.id, world.staff_b.id,
                                                 expected_version=appeal.version))
    with pytest.raises(Forbidden):
        run(app_engine, lambda svc: svc.citizen_reply(world.other_citizen_id, appeal.id, "mənim deyil"))


def test_no_actions_on_final_status(app_engine, world):
    appeal = new_appeal(app_engine, world)
    appeal, notes = run(app_engine, lambda svc: svc.redirect(
        world.coordinator, appeal.id, target="Su Kanal İdarəsi", letter_ref="12/345",
        expected_version=appeal.version))
    assert appeal.status == S.redirected and appeal.redirect_letter_ref == "12/345"
    assert notes.names() == ["redirected"]
    with pytest.raises(InvalidTransition):
        run(app_engine, lambda svc: svc.assign(world.coordinator, appeal.id, world.staff_a.id,
                                               expected_version=appeal.version))


def test_not_review_records_reason(app_engine, world):
    appeal = new_appeal(app_engine, world)
    appeal, notes = run(app_engine, lambda svc: svc.not_review(
        world.coordinator, appeal.id, reason="Məhkəmənin qanuni qüvvəyə minmiş qərarı var (m. 8.1.3)",
        expected_version=appeal.version))
    assert appeal.status == S.not_reviewed and appeal.closed_at == MONDAY_10
    assert "8.1.3" in notes.last("not_reviewed")["reason"]


# ---------- uzatma, rəy, deaktivasiya ----------
def test_extension_respects_30_workday_total_limit(app_engine, world):
    appeal = assigned_appeal(app_engine, world)
    appeal, notes = run(app_engine, lambda svc: svc.extend(
        world.coordinator, appeal.id, workdays=20, reason="Sorğu göndərilib", expected_version=appeal.version))
    assert appeal.due_date == CAL.add_workdays(date(2026, 11, 6), 20, count_start=False)
    assert appeal.original_due_date == date(2026, 11, 6) and notes.names() == ["extended"]
    with pytest.raises(ValidationFailed, match="10.2"):  # 20 + 15 > 30
        run(app_engine, lambda svc: svc.extend(world.coordinator, appeal.id, workdays=15, reason="yenə",
                                               expected_version=appeal.version))


def test_negative_feedback_reopens(app_engine, world):
    appeal = assigned_appeal(app_engine, world)
    appeal, _ = run(app_engine, lambda svc: svc.submit_answer(
        world.staff_a, appeal.id, outcome=Outcome.satisfied, text="Bərpa olundu", qa_critical=False,
        expected_version=appeal.version))
    appeal, notes = run(app_engine, lambda svc: svc.citizen_feedback(
        world.citizen_id, appeal.id, satisfied=False, reason="İşıqlar yenə yanmır"))
    assert (appeal.status, appeal.reopen_count, appeal.satisfaction) == (S.new, 1, -1)
    assert appeal.assigned_staff_id is None and appeal.outcome is None
    assert notes.last("reopened")["reason"] == "İşıqlar yenə yanmır"


def test_deactivating_staff_releases_open_work(app_engine, world):
    plain = assigned_appeal(app_engine, world)
    waiting = assigned_appeal(app_engine, world)
    waiting, _ = run(app_engine, lambda svc: svc.ask_citizen(world.staff_a, waiting.id, "Ünvan?",
                                                             expected_version=waiting.version))
    pending = assigned_appeal(app_engine, world, category=Category.complaint)
    pending, _ = run(app_engine, lambda svc: svc.submit_answer(
        world.staff_a, pending.id, outcome=Outcome.satisfied, text="Cavab", qa_critical=False,
        expected_version=pending.version))

    released, _ = run(app_engine, lambda svc: svc.deactivate_staff(world.coordinator, world.staff_a.id))
    assert sorted(released) == sorted([plain.id, waiting.id])
    with Session(app_engine) as s:
        assert s.get(Appeal, plain.id).status == S.new and s.get(Appeal, plain.id).assigned_staff_id is None
        assert s.get(Appeal, pending.id).status == S.pending_approval  # cavab artıq yazılıb

    pending, notes = run(app_engine, lambda svc: svc.send_back(world.coordinator, pending.id, "Düzəlt",
                                                               expected_version=pending.version))
    assert pending.status == S.new and notes.names() == ["released"]


def test_deactivation_requires_admin_and_not_self(app_engine, world):
    with pytest.raises(Forbidden):
        run(app_engine, lambda svc: svc.deactivate_staff(world.deputy, world.staff_a.id))
    with pytest.raises(ValidationFailed):
        run(app_engine, lambda svc: svc.deactivate_staff(world.coordinator, world.coordinator.id))


def test_auto_close_after_seven_days(app_engine, world):
    old = assigned_appeal(app_engine, world)
    run(app_engine, lambda svc: svc.submit_answer(world.staff_a, old.id, outcome=Outcome.satisfied, text="a",
                                                  qa_critical=False, expected_version=old.version))
    fresh = assigned_appeal(app_engine, world)
    run(app_engine, lambda svc: svc.submit_answer(world.staff_a, fresh.id, outcome=Outcome.satisfied, text="b",
                                                  qa_critical=False, expected_version=fresh.version),
        Clock(MONDAY_10 + timedelta(days=5)))
    closed, _ = run(app_engine, lambda svc: svc.auto_close(), Clock(MONDAY_10 + timedelta(days=7, minutes=1)))
    assert closed == 1
    with Session(app_engine) as s:
        assert (s.get(Appeal, old.id).status, s.get(Appeal, fresh.id).status) == (S.closed, S.answered)


# ---------- şəxsi məlumat, mətn, audit ----------
def test_reveal_fin_is_audited_without_storing_the_value(app_engine, world):
    appeal = assigned_appeal(app_engine, world)
    fin, _ = run(app_engine, lambda svc: svc.reveal_fin(world.staff_a, appeal.id))
    assert fin == "5ABC12D"
    with Session(app_engine) as s:
        event = s.scalar(select(AppealEvent).where(AppealEvent.action == "pii_viewed"))
        assert event.actor_id == world.staff_a.id and "5ABC12D" not in str(event.payload)
    with pytest.raises(Forbidden):
        run(app_engine, lambda svc: svc.reveal_fin(world.staff_b, appeal.id))


def test_citizen_extra_info_keeps_status(app_engine, world):
    appeal = assigned_appeal(app_engine, world)
    appeal, notes = run(app_engine, lambda svc: svc.citizen_reply(world.citizen_id, appeal.id, "Foto da var"))
    assert appeal.status == S.assigned and notes.names() == ["citizen_replied"]
    with Session(app_engine) as s:
        assert s.scalar(select(AppealMessage.kind).where(AppealMessage.appeal_id == appeal.id)) == \
            MessageKind.citizen_reply


def test_text_length_limit(app_engine, world):
    appeal = assigned_appeal(app_engine, world)
    with pytest.raises(ValidationFailed, match="4000"):
        run(app_engine, lambda svc: svc.submit_answer(world.staff_a, appeal.id, outcome=Outcome.satisfied,
                                                      text="ə" * 4001, qa_critical=False,
                                                      expected_version=appeal.version))


def test_suggest_assignee_rules(app_engine, world):
    with Session(app_engine) as s:
        assert suggest_assignee(s, world.pensiya_id).id == world.staff_a.id  # bərabərdir → id kiçik olan
        general_id = s.scalar(select(Department.id).where(Department.code == "general"))
        assert suggest_assignee(s, general_id) is None  # Ümumi şöbədə əməkdaş yoxdur
