"""Model → JSON for the API. Media paths become short-lived signed URLs; secrets never leave the server."""

from __future__ import annotations

from ..config import PLATFORM_SPECS
from ..models import Account, DedupReport, Delivery, HistoryItem, Material, Project, PublishBatch, Shot, Variant, Job
from ..security import signed_media_url


def dt(v):
    return v.isoformat() + "Z" if v else None


def material(m: Material, shots: bool = False) -> dict:
    d = {"id": m.id, "filename": m.filename, "status": m.status, "error": m.error, "product": m.product,
         "tags": m.tags or [], "license_type": m.license_type, "license_note": m.license_note,
         "publishable": m.publishable, "duration": round(m.duration, 2), "width": m.width, "height": m.height,
         "fps": m.fps, "hdr": m.hdr, "codec": m.codec, "has_audio": m.has_audio, "size_bytes": m.size_bytes,
         "source_group": m.source_group, "thumb_url": signed_media_url(m.thumb_path),
         "proxy_url": signed_media_url(m.proxy_path), "shot_count": len(m.shots), "created_at": dt(m.created_at)}
    if shots:
        d["shots"] = [shot(s) for s in m.shots]
        d["analysis"] = {k: m.analysis.get(k) for k in ("fps", "motion", "sharp", "sat", "warm")} if m.analysis else {}
    return d


def shot(s: Shot) -> dict:
    return {"id": s.id, "idx": s.idx, "start": s.start, "end": s.end, "duration": round(s.end - s.start, 2),
            "motion": s.motion, "sharpness": s.sharpness, "vivid": s.vivid, "eye_catch": s.eye_catch,
            "tags": s.tags or [], "thumb_url": signed_media_url(s.thumb_path)}


def project(p: Project, with_variants: bool = False) -> dict:
    counts: dict[str, int] = {}
    for v in p.variants:
        counts[v.status] = counts.get(v.status, 0) + 1
    d = {"id": p.id, "name": p.name, "product": p.product, "material_ids": p.material_ids, "platforms": p.platforms,
         "options": p.options, "status": p.status, "error": p.error, "variant_counts": counts,
         "created_at": dt(p.created_at), "updated_at": dt(p.updated_at)}
    if with_variants:
        d["variants"] = [variant(v) for v in p.variants]
    return d


def variant(v: Variant, full: bool = False) -> dict:
    d = {"id": v.id, "project_id": v.project_id, "platform": v.platform,
         "platform_name": PLATFORM_SPECS.get(v.platform, {}).get("name", v.platform), "seq": v.seq,
         "template_id": v.template_id, "status": v.status, "error": v.error, "copy": v.copywriting,
         "style": (v.plan or {}).get("style", {}),
         "dedup_decision": v.dedup_decision, "dedup_score": v.dedup_score, "duration": round(v.duration, 2),
         "width": v.width, "height": v.height, "size_bytes": v.size_bytes, "render_backend": v.render_backend,
         "render_seconds": v.render_seconds, "video_url": signed_media_url(v.file_path),
         "cover_url": signed_media_url(v.cover_path), "editor_project_path": v.editor_project_path,
         "approved_by": v.approved_by, "approved_at": dt(v.approved_at), "created_at": dt(v.created_at)}
    if full:
        d["plan"] = v.plan
        d["plan_report"] = v.plan_report
    return d


def report(r: DedupReport) -> dict:
    return {"id": r.id, "variant_id": r.variant_id, "decision": r.decision, "score": r.score, "metrics": r.metrics,
            "matches": r.matches, "scope": r.scope, "compared": r.compared, "reviewed_by": r.reviewed_by,
            "review_decision": r.review_decision, "review_note": r.review_note, "reviewed_at": dt(r.reviewed_at),
            "created_at": dt(r.created_at)}


def history(h: HistoryItem) -> dict:
    return {"id": h.id, "title": h.title, "platform": h.platform, "status": h.status, "error": h.error,
            "duration": round(h.duration, 2), "published_at": dt(h.published_at), "created_at": dt(h.created_at),
            "video_url": signed_media_url(h.storage_path)}


def account(a: Account) -> dict:
    return {"id": a.id, "platform": a.platform, "platform_name": PLATFORM_SPECS.get(a.platform, {}).get("name"),
            "display_name": a.display_name, "mode": a.mode, "open_id": a.open_id, "status": a.status,
            "has_token": bool(a.access_token_enc), "token_expires_at": dt(a.token_expires_at),
            "refresh_expires_at": dt(a.refresh_expires_at), "scopes": a.scopes, "daily_limit": a.daily_limit,
            "min_interval_s": a.min_interval_s, "last_published_at": dt(a.last_published_at), "note": a.note,
            "created_at": dt(a.created_at)}


def delivery(d: Delivery, events: bool = False) -> dict:
    out = {"id": d.id, "batch_id": d.batch_id, "variant_id": d.variant_id, "account_id": d.account_id,
           "platform": d.platform, "platform_name": PLATFORM_SPECS.get(d.platform, {}).get("name"), "mode": d.mode,
           "state": d.state, "title": d.title, "text": d.text, "platform_post_id": d.platform_post_id,
           "share_url": d.share_url, "attempts": d.attempts, "error_class": d.error_class, "last_error": d.last_error,
           "scheduled_at": dt(d.scheduled_at), "submitted_at": dt(d.submitted_at), "published_at": dt(d.published_at),
           "operator": d.operator, "created_at": dt(d.created_at), "updated_at": dt(d.updated_at)}
    if events:
        out["events"] = [{"from": e.from_state, "to": e.to_state, "message": e.message, "data": e.data, "at": dt(e.at)}
                         for e in d.events]
    return out


def batch(b: PublishBatch, deliveries: bool = True) -> dict:
    d = {"id": b.id, "project_id": b.project_id, "status": b.status, "preflight": b.preflight, "summary": b.summary,
         "requested_by": b.requested_by, "created_at": dt(b.created_at)}
    if deliveries:
        d["deliveries"] = [delivery(x) for x in b.deliveries]
    return d


def error_brief(text: str) -> str:
    """The exception message. A tail slice of the stored traceback starts mid-frame and hides the cause."""
    if not text:
        return ""
    head = text.split("Traceback (most recent call last):", 1)[0].strip()
    if head:
        return head.splitlines()[-1][:800]
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in reversed(lines):
        if ln.startswith("File ") or ln.startswith("^"):
            continue
        return ln[:800]
    return text.strip()[:800]


def job(j: Job) -> dict:
    return {"id": j.id, "queue": j.queue, "kind": j.kind, "payload": j.payload, "status": j.status,
            "attempts": j.attempts, "progress": j.progress, "message": j.message, "last_error": error_brief(j.last_error),
            "run_after": dt(j.run_after), "created_at": dt(j.created_at), "finished_at": dt(j.finished_at)}
