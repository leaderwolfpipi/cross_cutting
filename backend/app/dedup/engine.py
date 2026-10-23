"""Dedup engine: fingerprints + three-layer comparison against the account's own history (docs/design.md §6)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..models import DedupReport, Fingerprint, HistoryItem, Material, Variant
from . import audio_fp, matcher, phash, provenance

log = logging.getLogger(__name__)

# variants that count as "history" for new remixes (rejected / discarded / failed ones never went out)
COMPARABLE = ("passed", "review", "approved")


@dataclass
class FP:
    hashes: np.ndarray  # (n, crops) uint64
    info: np.ndarray
    audio: np.ndarray
    duration: float


def compute(path: str | Path, with_audio: bool = True) -> FP:
    hashes, info = phash.video_hashes(path)
    audio = audio_fp.fingerprint_file(path) if with_audio else np.zeros(0, np.uint32)
    return FP(hashes, info, audio, len(info) * phash.FRAME_STEP)


def save(db: Session, owner_type: str, owner_id: str, fp: FP, platform: str = "") -> Fingerprint:
    row = db.scalar(select(Fingerprint).where(Fingerprint.owner_type == owner_type, Fingerprint.owner_id == owner_id))
    row = row or Fingerprint(owner_type=owner_type, owner_id=owner_id)
    row.platform = platform
    row.duration = fp.duration
    row.frame_step = phash.FRAME_STEP
    row.frame_hashes = phash.pack(fp.hashes, "<u8")
    row.frame_info = phash.pack(fp.info, "u1")
    row.audio_step = audio_fp.STEP
    row.audio_hashes = phash.pack(fp.audio, "<u4")
    db.add(row)
    db.flush()
    return row


def load(row: Fingerprint) -> FP:
    flat = phash.unpack(row.frame_hashes, "<u8").astype(np.uint64)
    info = phash.unpack(row.frame_info, "u1")
    cols = len(phash.CROPS)
    hashes = flat.reshape(-1, cols) if len(info) and len(flat) == len(info) * cols else flat
    return FP(hashes, info, phash.unpack(row.audio_hashes, "<u4").astype(np.uint32), row.duration)


# ---------------------------------------------------------------------- scope
def history_variants(db: Session, platform: str, exclude_id: str = "") -> list[Variant]:
    s = get_settings()
    q = select(Variant).where(Variant.status.in_(COMPARABLE), Variant.id != exclude_id)
    if s.dedup_scope == "platform":
        q = q.where(Variant.platform == platform)
    return list(db.scalars(q))


def history_items(db: Session, platform: str) -> list[HistoryItem]:
    q = select(HistoryItem).where(HistoryItem.status == "ready")
    if get_settings().dedup_scope == "platform":
        q = q.where(or_(HistoryItem.platform == platform, HistoryItem.platform == ""))
    return list(db.scalars(q))


def usage_ledger(db: Session, platform: str) -> provenance.UsageLedger:
    s = get_settings()
    ledger = provenance.UsageLedger(s.freshness_half_life_days, now=utcnow())
    for v in history_variants(db, platform):
        ledger.add(v.plan, v.created_at)
    return ledger


def plan_overlap(db: Session, plan: dict, platform: str, exclude_id: str = "") -> dict:
    """L1 check before rendering: worst pairwise source reuse and library reuse against in-scope history."""
    hist = history_variants(db, platform, exclude_id)
    pairs = sorted(((provenance.reuse(plan, provenance.footprint(v.plan)), v.id) for v in hist), reverse=True)
    library = provenance.reuse(plan, provenance.union_footprint(provenance.footprint(v.plan) for v in hist))
    worst = pairs[0] if pairs else (0.0, "")
    return {"max_pair": round(worst[0], 4), "max_pair_variant": worst[1], "library": round(library, 4),
            "compared": len(hist)}


# ---------------------------------------------------------------------- decision
def decide(content: float, score: float, library: float) -> str:
    s = get_settings()
    if content >= s.dedup_reject_content or score >= s.dedup_reject_score:
        return "reject"
    if content >= s.dedup_review_content or score >= s.dedup_review_score or library >= s.dedup_review_library:
        return "review"
    return "pass"


def check_variant(db: Session, variant: Variant, fp: FP) -> DedupReport:
    s = get_settings()
    hist_vars = history_variants(db, variant.platform, variant.id)
    hist_items = history_items(db, variant.platform)
    rows = {(r.owner_type, r.owner_id): r for r in db.scalars(select(Fingerprint).where(
        or_(Fingerprint.owner_id.in_([v.id for v in hist_vars] or [""]),
            Fingerprint.owner_id.in_([h.id for h in hist_items] or [""]))))}
    refs, ref_fp, meta = [], {}, {}
    for v in hist_vars:
        r = rows.get(("variant", v.id))
        if r:
            f = load(r)
            refs.append(matcher.Ref("variant", v.id, f.hashes, f.info))
            ref_fp[v.id] = f
        meta[v.id] = {"type": "variant", "platform": v.platform, "project_id": v.project_id,
                      "title": (v.copywriting or {}).get("title", ""), "status": v.status, "plan": v.plan}
    for h in hist_items:
        r = rows.get(("history", h.id))
        if r:
            f = load(r)
            refs.append(matcher.Ref("history", h.id, f.hashes, f.info))
            ref_fp[h.id] = f
            meta[h.id] = {"type": "history", "platform": h.platform, "title": h.title, "plan": None}

    pair_v, lib_v = matcher.coverage_against(fp.hashes, fp.info, refs, max_dist=s.dedup_hamming)
    by_id = {p.owner_id: p for p in pair_v}
    hist_plans = [m["plan"] for m in meta.values() if m.get("plan")]
    lib_p = provenance.reuse(variant.plan, provenance.union_footprint(provenance.footprint(p) for p in hist_plans))

    cands = []
    for oid, m in meta.items():
        P = provenance.reuse(variant.plan, provenance.footprint(m["plan"])) if m.get("plan") else 0.0
        pv = by_id.get(oid)
        V = pv.coverage if pv else 0.0
        cands.append({"owner_id": oid, "owner_type": m["type"], "title": m["title"], "platform": m["platform"],
                      "P": round(P, 4), "V": round(V, 4), "A": 0.0, "segments": (pv.segments if pv else [])[:6]})
    # audio is the slow layer: only for the most similar candidates and those sharing the same BGM
    bgm_id = ((variant.plan.get("audio") or {}).get("bgm") or {}).get("bgm_id")
    cands.sort(key=lambda c: -max(c["P"], c["V"]))
    for i, c in enumerate(cands):
        same_bgm = bool(bgm_id) and bgm_id == (((meta[c["owner_id"]].get("plan") or {}).get("audio") or {})
                                               .get("bgm") or {}).get("bgm_id")
        if (i < 10 or same_bgm) and c["owner_id"] in ref_fp:
            c["A"] = round(audio_fp.coverage(fp.audio, ref_fp[c["owner_id"]].audio), 4)
        c["content"] = round(max(c["P"], c["V"]), 4)
        c["score"] = round(0.75 * c["content"] + 0.25 * c["A"], 4)
    cands.sort(key=lambda c: -c["score"])
    top = cands[0] if cands else {"content": 0.0, "score": 0.0}
    library = max(lib_v, lib_p)
    decision = decide(top["content"], top["score"], library)
    report = DedupReport(
        variant_id=variant.id, decision=decision, score=top["score"], scope=s.dedup_scope, compared=len(meta),
        metrics={"content": top["content"], "score": top["score"], "library_visual": lib_v,
                 "library_provenance": round(lib_p, 4), "library": round(library, 4),
                 "thresholds": {"reject_content": s.dedup_reject_content, "reject_score": s.dedup_reject_score,
                                "review_content": s.dedup_review_content, "review_score": s.dedup_review_score,
                                "review_library": s.dedup_review_library},
                 "frames": int(len(fp.info)), "informative_frames": int(fp.info.sum()) if len(fp.info) else 0},
        matches=cands[:8])
    db.add(report)
    variant.dedup_decision = decision
    variant.dedup_score = top["score"]
    variant.status = {"pass": "passed", "review": "review", "reject": "rejected"}[decision]
    db.flush()
    return report


# ---------------------------------------------------------------------- materials
def assign_source_group(db: Session, material: Material, fp: FP) -> str:
    """Re-encoded / trimmed copies of the same footage share a source_group, so provenance dedup can't be
    bypassed by re-uploading a file."""
    others = list(db.scalars(select(Material).where(Material.id != material.id, Material.status == "ready")))
    rows = {r.owner_id: r for r in db.scalars(select(Fingerprint).where(
        Fingerprint.owner_type == "material", Fingerprint.owner_id.in_([m.id for m in others] or [""])))}
    refs = [matcher.Ref("material", m.id, load(rows[m.id]).hashes, load(rows[m.id]).info) for m in others if m.id in rows]
    group = material.id
    if refs:
        pairs, _ = matcher.coverage_against(fp.hashes, fp.info, refs, max_dist=get_settings().dedup_hamming)
        best = max(pairs, key=lambda p: p.coverage)
        if best.coverage >= 0.6:
            group = next(m.source_group for m in others if m.id == best.owner_id) or best.owner_id
    material.source_group = group
    return group
