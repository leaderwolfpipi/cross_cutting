"""Job handlers. Each is idempotent: re-running a job after a crash continues from persisted state."""

from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy import select

from ..creative import vision
from ..db import SessionLocal, utcnow
from ..models import BgmTrack, HistoryItem, Material, Project, Variant
from ..publish import orchestrator
from ..quotes import service as quote_service
from ..services import generate, ingest
from . import queue

log = logging.getLogger(__name__)
Progress = Callable[[float, str], None]


def analyze_material(payload: dict, progress: Progress) -> dict:
    with SessionLocal() as db:
        m = db.get(Material, payload["material_id"])
        if m is None:
            return {"skipped": "deleted"}
        try:
            ingest.analyze_material(db, m, progress)
        except Exception as e:
            db.rollback()
            m = db.get(Material, payload["material_id"])
            m.status, m.error = "failed", str(e)[:2000]
            db.commit()
            raise
        if vision.wanted():
            queue.enqueue(db, "analyze", "describe_material", {"material_id": m.id},
                          dedupe_key=f"vision:{m.id}", max_attempts=3)
        return {"shots": len(m.shots), "source_group": m.source_group}


def describe_material(payload: dict, progress: Progress) -> dict:
    """Vision notes per shot (copy grounding). Optional: rendering never waits for it."""
    with SessionLocal() as db:
        m = db.get(Material, payload["material_id"])
        if m is None or m.status != "ready":
            return {"skipped": "not ready"}
        return {"described": vision.describe_material(db, m, progress, force=payload.get("force", False))}


def analyze_history(payload: dict, progress: Progress) -> dict:
    with SessionLocal() as db:
        h = db.get(HistoryItem, payload["history_id"])
        if h is None:
            return {"skipped": "deleted"}
        try:
            ingest.analyze_history(db, h, progress)
        except Exception as e:
            db.rollback()
            h = db.get(HistoryItem, payload["history_id"])
            h.status, h.error = "failed", str(e)[:2000]
            db.commit()
            raise
        return {"duration": h.duration}


def analyze_bgm(payload: dict, progress: Progress) -> dict:
    with SessionLocal() as db:
        t = db.get(BgmTrack, payload["bgm_id"])
        if t is None:
            return {"skipped": "deleted"}
        ingest.analyze_bgm(db, t, progress)
        return {"bpm": t.bpm, "beats": len(t.beats)}


def generate_variants(payload: dict, progress: Progress) -> dict:
    """Render one project's batch in order so all platforms see earlier siblings."""
    done, results = 0, {}
    ids = payload["variant_ids"]
    for i, vid in enumerate(ids):
        with SessionLocal() as db:
            v = db.get(Variant, vid)
            if v is None or v.status not in ("queued", "planning", "rendering", "checking", "failed"):
                continue  # finished in a previous run of this job
            sub = lambda f, m, i=i: progress((i + f) / len(ids), m)  # noqa: E731
            try:
                v = generate.generate_variant(db, v, sub)
                results[vid] = v.status
            except Exception as e:  # one bad variant must not stop its siblings
                log.exception("variant %s failed", vid)
                db.rollback()
                v = db.get(Variant, vid)
                v.status, v.error = "failed", str(e)[:2000]
                db.commit()
                results[vid] = "failed"
            done += 1
    with SessionLocal() as db:
        p = db.get(Project, payload["project_id"])
        if p is not None:
            active = db.scalar(select(Variant.id).where(Variant.project_id == p.id, Variant.status.in_(
                ("queued", "planning", "rendering", "checking"))))
            if not active:
                p.status = "ready"
                p.updated_at = utcnow()
                db.commit()
    return {"done": done, "results": results}


def recheck_variant(payload: dict, progress: Progress) -> dict:
    with SessionLocal() as db:
        v = db.get(Variant, payload["variant_id"])
        generate.recheck(db, v)
        return {"decision": v.dedup_decision}


def publish_step(payload: dict, progress: Progress) -> dict:
    orchestrator.step(payload["delivery_id"])
    return {}


def prepare_quote_publish(payload: dict, progress: Progress) -> dict:
    with SessionLocal() as db:
        return quote_service.prepare_and_publish(db, payload["sheet_id"], payload["revision"],
                                                 payload["categories"], payload["account_ids"],
                                                 payload["requested_by"], progress)


HANDLERS: dict[str, Callable[[dict, Progress], dict]] = {
    "analyze_material": analyze_material,
    "describe_material": describe_material,
    "analyze_history": analyze_history,
    "analyze_bgm": analyze_bgm,
    "generate_variants": generate_variants,
    "recheck_variant": recheck_variant,
    "publish_step": publish_step,
    "prepare_quote_publish": prepare_quote_publish,
}
