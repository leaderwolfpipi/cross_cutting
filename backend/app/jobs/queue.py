"""Durable job queue on the application database (replaces Celery/Redis).

- claim: optimistic `UPDATE ... WHERE id=? AND status='queued'` — portable across SQLite and PostgreSQL, and two
  workers can never both own a job.
- leases + heartbeats: a crashed worker's job returns to the queue when its lease expires (reaper), up to
  max_attempts, then it is marked dead.
- dedupe_key: at most one queued/running job per key (partial unique index), so double-clicks and retries never
  run the same publish step twice concurrently.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import SessionLocal, utcnow
from ..models import Job

log = logging.getLogger(__name__)

QUEUES = ("analyze", "render", "dedup", "publish")


class Retry(Exception):
    """Raise from a handler to re-run the job later without counting it as a failure."""

    def __init__(self, delay_s: float, message: str = ""):
        super().__init__(message or f"retry in {delay_s:.0f}s")
        self.delay_s = delay_s


def enqueue(db: Session, queue: str, kind: str, payload: dict[str, Any] | None = None, *, dedupe_key: str | None = None,
            run_after: datetime | None = None, priority: int = 0, max_attempts: int = 3, commit: bool = True) -> Job | None:
    """Insert a job. Returns None when an active job with the same dedupe_key already exists."""
    assert queue in QUEUES, queue
    job = Job(queue=queue, kind=kind, payload=payload or {}, dedupe_key=dedupe_key, run_after=run_after or utcnow(),
              priority=priority, max_attempts=max_attempts)
    if dedupe_key:
        existing = db.scalar(select(Job).where(Job.dedupe_key == dedupe_key, Job.status.in_(("queued", "running"))))
        if existing:
            return None
    db.add(job)
    if not commit:
        db.flush()
        return job
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # lost a race with another enqueue of the same key
        return None
    return job


def claim(worker_id: str, queues: tuple[str, ...], lease_s: int, limit: int = 1) -> list[int]:
    now = utcnow()
    claimed: list[int] = []
    with SessionLocal() as db:
        ids = db.scalars(select(Job.id).where(Job.status == "queued", Job.queue.in_(queues), Job.run_after <= now)
                         .order_by(Job.priority.desc(), Job.run_after, Job.id).limit(limit * 4)).all()
        for jid in ids:
            res = db.execute(update(Job).where(Job.id == jid, Job.status == "queued")
                             .values(status="running", locked_by=worker_id, lease_until=now + timedelta(seconds=lease_s),
                                     attempts=Job.attempts + 1, updated_at=now))
            db.commit()
            if res.rowcount == 1:
                claimed.append(jid)
                if len(claimed) >= limit:
                    break
    return claimed


def heartbeat(job_id: int, worker_id: str, lease_s: int, progress: float | None = None, message: str | None = None) -> None:
    values: dict[str, Any] = {"lease_until": utcnow() + timedelta(seconds=lease_s)}
    if progress is not None:
        values["progress"] = round(progress, 3)
    if message is not None:
        values["message"] = message[:500]
    with SessionLocal() as db:
        db.execute(update(Job).where(Job.id == job_id, Job.locked_by == worker_id, Job.status == "running").values(**values))
        db.commit()


def finish(job_id: int, worker_id: str, result: dict | None = None) -> None:
    with SessionLocal() as db:
        db.execute(update(Job).where(Job.id == job_id, Job.locked_by == worker_id)
                   .values(status="succeeded", result=result or {}, progress=1.0, finished_at=utcnow(), lease_until=None))
        db.commit()


def fail(job_id: int, worker_id: str, error: str, retry_delay_s: float | None = None, count_attempt: bool = True) -> str:
    """Record a failure; requeue with backoff while attempts remain. Returns the new status."""
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None or job.locked_by != worker_id:
            return "lost"
        if not count_attempt:
            job.attempts = max(0, job.attempts - 1)
        if job.attempts < job.max_attempts:
            delay = retry_delay_s if retry_delay_s is not None else min(600.0, 5 * 2 ** job.attempts) * random.uniform(0.8, 1.2)
            job.status, job.run_after = "queued", utcnow() + timedelta(seconds=delay)
        else:
            job.status, job.finished_at = "dead", utcnow()
        job.last_error = error[-4000:]
        job.lease_until = None
        job.locked_by = ""
        db.commit()
        return job.status


def reap_expired() -> int:
    """Requeue jobs whose worker vanished (lease expired)."""
    now = utcnow()
    with SessionLocal() as db:
        expired = db.scalars(select(Job).where(Job.status == "running", Job.lease_until < now)).all()
        for job in expired:
            job.last_error = (job.last_error + f"\nlease expired (worker {job.locked_by})")[-4000:]
            job.locked_by, job.lease_until = "", None
            if job.attempts < job.max_attempts:
                job.status, job.run_after = "queued", now
            else:
                job.status, job.finished_at = "dead", now
        db.commit()
        if expired:
            log.warning("reaped %d expired job(s)", len(expired))
        return len(expired)
