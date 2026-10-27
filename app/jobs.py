"""Agent işlərinin növbəsi (jobs): triaj, cavab layihəsi. Server restart olsa da iş itmir.

Handler-lər iş növünə görə qeydiyyatdan keçir (Faza 5: "triage", "draft"). Handler-i olmayan iş
götürülmür və növbədə gözləyir.

Handler-ə ORM obyekti yox, işin snapshot-u verilir və handler tranzaksiyanı özü idarə edir.
Səbəb: agentin LLM çağırışı saniyələrlə çəkir, bu müddətdə DB kilidi saxlanmamalıdır.
"""
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.db.tables import Job
from app.workflow.states import JobStatus

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobSnapshot:
    id: int
    kind: str
    appeal_id: int | None
    payload: dict | None


JobHandler = Callable[[JobSnapshot], None]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JobRunner:
    def __init__(self, session_factory: sessionmaker[Session], handlers: Mapping[str, JobHandler], *,
                 retry_delays: list[int], stale_minutes: int, clock: Callable[[], datetime] = utc_now):
        self._sessions = session_factory
        self._handlers = handlers
        self._delays = retry_delays
        self._stale = timedelta(minutes=stale_minutes)
        self._clock = clock

    def process_batch(self, limit: int = 5) -> int:
        if not self._handlers:
            return 0
        self._recover_stale()
        claimed = self._claim(limit)
        for snapshot in claimed:
            self._run(snapshot)
        return len(claimed)

    def _recover_stale(self) -> None:
        with self._sessions() as s, s.begin():
            s.execute(
                update(Job)
                .where(Job.status == JobStatus.running, Job.next_attempt_at < self._clock() - self._stale)
                .values(status=JobStatus.pending)
            )

    def _claim(self, limit: int) -> list[JobSnapshot]:
        now = self._clock()
        with self._sessions() as s, s.begin():
            jobs = s.scalars(
                select(Job)
                .where(Job.status == JobStatus.pending, Job.next_attempt_at <= now, Job.kind.in_(list(self._handlers)))
                .order_by(Job.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            for job in jobs:
                job.status, job.attempts, job.next_attempt_at = JobStatus.running, job.attempts + 1, now
            return [JobSnapshot(j.id, j.kind, j.appeal_id, dict(j.payload) if j.payload else None) for j in jobs]

    def _run(self, snapshot: JobSnapshot) -> None:
        try:
            self._handlers[snapshot.kind](snapshot)
        except Exception as e:  # bir işin xətası digərlərini dayandırmamalıdır
            log.warning("iş %s (%s) alınmadı: %s", snapshot.id, snapshot.kind, e)
            self._failed(snapshot.id, f"{type(e).__name__}: {e}")
        else:
            with self._sessions() as s, s.begin():
                job = s.get(Job, snapshot.id, with_for_update=True)
                job.status, job.finished_at, job.last_error = JobStatus.done, self._clock(), None

    def _failed(self, job_id: int, error: str) -> None:
        with self._sessions() as s, s.begin():
            job = s.get(Job, job_id, with_for_update=True)
            job.last_error = error[:1000]
            if job.attempts > len(self._delays):
                job.status = JobStatus.dead
                log.error("iş %s ölü statusa keçdi: %s", job_id, error)
            else:
                job.status = JobStatus.pending
                job.next_attempt_at = self._clock() + timedelta(seconds=self._delays[job.attempts - 1])
