"""Agent işlərinin növbəsi: uğur, təkrar cəhd, ölü status, handler-i olmayan iş, restart bərpası."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.db.tables import Job
from app.jobs import JobRunner
from app.workflow.states import JobStatus

pytestmark = pytest.mark.integration

T0 = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)
DELAYS = [60, 300]


class Clock:
    def __init__(self, moment):
        self.moment = moment

    def __call__(self):
        return self.moment


@pytest.fixture
def factory(app_engine):
    return sessionmaker(app_engine, expire_on_commit=False)


def add_job(factory, kind="triage", payload=None) -> int:
    with factory() as s, s.begin():
        job = Job(kind=kind, payload=payload, next_attempt_at=T0)
        s.add(job)
        s.flush()
        return job.id


def job(factory, job_id) -> Job:
    with factory() as s:
        return s.get(Job, job_id)


def test_successful_job_is_done(factory):
    seen = []
    job_id = add_job(factory, payload={"x": 1})
    runner = JobRunner(factory, {"triage": lambda j: seen.append((j.id, j.kind, j.payload))}, retry_delays=DELAYS,
                       stale_minutes=10, clock=Clock(T0))
    assert runner.process_batch() == 1
    assert seen == [(job_id, "triage", {"x": 1})]
    assert job(factory, job_id).status == JobStatus.done and job(factory, job_id).finished_at == T0


def test_failing_job_is_retried_then_dead(factory):
    job_id = add_job(factory)
    clock = Clock(T0)

    def boom(_):
        raise RuntimeError("LLM cavab vermədi")

    runner = JobRunner(factory, {"triage": boom}, retry_delays=DELAYS, stale_minutes=10, clock=clock)
    for delay in DELAYS:
        runner.process_batch()
        row = job(factory, job_id)
        assert row.status == JobStatus.pending and row.next_attempt_at == clock.moment + timedelta(seconds=delay)
        clock.moment = row.next_attempt_at
    runner.process_batch()
    row = job(factory, job_id)
    assert row.status == JobStatus.dead and "LLM cavab vermədi" in row.last_error


def test_job_without_handler_is_left_alone(factory):
    job_id = add_job(factory, kind="draft")  # Faza 5-ə qədər handler yoxdur
    runner = JobRunner(factory, {"triage": lambda j: None}, retry_delays=DELAYS, stale_minutes=10, clock=Clock(T0))
    assert runner.process_batch() == 0
    assert job(factory, job_id).status == JobStatus.pending and job(factory, job_id).attempts == 0


def test_stale_running_job_is_recovered(factory):
    job_id = add_job(factory)
    with factory() as s, s.begin():  # worker iş ortasında çöküb
        s.execute(text("UPDATE jobs SET status = 'running', next_attempt_at = :t"), {"t": T0 - timedelta(minutes=11)})
    seen = []
    JobRunner(factory, {"triage": lambda j: seen.append(j.id)}, retry_delays=DELAYS, stale_minutes=10,
              clock=Clock(T0)).process_batch()
    assert seen == [job_id] and job(factory, job_id).status == JobStatus.done


def test_handler_failure_does_not_stop_other_jobs(factory):
    first, second = add_job(factory, payload={"fail": True}), add_job(factory)

    def handler(j):
        if (j.payload or {}).get("fail"):
            raise ValueError("pis iş")

    JobRunner(factory, {"triage": handler}, retry_delays=DELAYS, stale_minutes=10, clock=Clock(T0)).process_batch()
    assert job(factory, first).status == JobStatus.pending and job(factory, second).status == JobStatus.done
