"""Durable job queue: exclusive claim, dedupe, retry, lease expiry."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import update

from app.db import SessionLocal, utcnow
from app.jobs import queue
from app.models import Job


def _clear():
    with SessionLocal() as db:
        db.query(Job).delete()
        db.commit()


def test_claim_is_exclusive_and_dedupe_blocks_duplicates(settings):
    _clear()
    with SessionLocal() as db:
        assert queue.enqueue(db, "publish", "publish_step", {"delivery_id": "d1"}, dedupe_key="pub:d1")
        assert queue.enqueue(db, "publish", "publish_step", {"delivery_id": "d1"}, dedupe_key="pub:d1") is None
    a = queue.claim("w1", ("publish",), 60)
    b = queue.claim("w2", ("publish",), 60)
    assert len(a) == 1 and b == []
    queue.finish(a[0], "w1", {"ok": True})
    with SessionLocal() as db:  # finished → the key is free again
        assert queue.enqueue(db, "publish", "publish_step", {"delivery_id": "d1"}, dedupe_key="pub:d1")


def test_failure_requeues_then_dies(settings):
    _clear()
    with SessionLocal() as db:
        job = queue.enqueue(db, "analyze", "analyze_material", {}, max_attempts=2)
    for expected in ("queued", "dead"):
        with SessionLocal() as db:  # make it runnable now
            db.execute(update(Job).where(Job.id == job.id).values(run_after=utcnow()))
            db.commit()
        (jid,) = queue.claim("w", ("analyze",), 60)
        assert queue.fail(jid, "w", "boom") == expected


def test_retry_does_not_consume_attempts(settings):
    _clear()
    with SessionLocal() as db:
        job = queue.enqueue(db, "publish", "publish_step", {}, max_attempts=1)
    for _ in range(3):
        with SessionLocal() as db:
            db.execute(update(Job).where(Job.id == job.id).values(run_after=utcnow()))
            db.commit()
        (jid,) = queue.claim("w", ("publish",), 60)
        assert queue.fail(jid, "w", "poll", retry_delay_s=0, count_attempt=False) == "queued"


def test_expired_lease_is_reaped(settings):
    _clear()
    with SessionLocal() as db:
        queue.enqueue(db, "render", "generate_variants", {})
    (jid,) = queue.claim("dead-worker", ("render",), 60)
    with SessionLocal() as db:
        db.execute(update(Job).where(Job.id == jid).values(lease_until=utcnow() - timedelta(seconds=1)))
        db.commit()
    assert queue.reap_expired() == 1
    assert queue.claim("w2", ("render",), 60) == [jid]
