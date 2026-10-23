"""Projects, variants and dedup reports."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import PLATFORM_SPECS, get_settings
from ..creative.templates import TEMPLATES
from ..db import get_db, utcnow
from ..jobs import queue
from ..models import DedupReport, Delivery, Fingerprint, Material, Project, Variant
from ..security import require_api_key
from ..services.generate import new_variants
from . import serializers as S

router = APIRouter(tags=["项目与成片"])


class ProductIn(BaseModel):
    name: str = "海鲜"
    items: list[str] = []
    selling_points: list[str] = []
    origin: str = ""
    price: str = ""
    store: str = ""
    city: str = ""
    cta: str = ""
    keywords: list[str] = []


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    product: ProductIn = ProductIn()
    material_ids: list[str] = []
    platforms: list[str] = list(PLATFORM_SPECS)
    options: dict = {}  # templates: [...], audio_mode: auto|original|bgm, bgm_ids, duration: [min, max]


def _validate(req: ProjectIn, db: Session) -> None:
    bad = [p for p in req.platforms if p not in PLATFORM_SPECS]
    if bad:
        raise HTTPException(422, f"unknown platforms {bad}")
    bad_t = [t for t in req.options.get("templates", []) if t not in TEMPLATES]
    if bad_t:
        raise HTTPException(422, f"unknown templates {bad_t}; available {list(TEMPLATES)}")
    if req.material_ids:
        found = set(db.scalars(select(Material.id).where(Material.id.in_(req.material_ids))))
        missing = set(req.material_ids) - found
        if missing:
            raise HTTPException(422, f"unknown materials {sorted(missing)}")


@router.get("/api/templates")
def templates():
    return [{"id": t.id, "name": t.name, "slots": [s.role for s in t.slots], "platform_weight": t.platform_weight}
            for t in TEMPLATES.values()]


@router.post("/api/projects")
def create_project(req: ProjectIn, db: Session = Depends(get_db)):
    _validate(req, db)
    p = Project(name=req.name, product=req.product.model_dump(), material_ids=req.material_ids,
                platforms=req.platforms, options=req.options)
    db.add(p)
    db.commit()
    return S.project(p)


@router.get("/api/projects")
def list_projects(db: Session = Depends(get_db)):
    return [S.project(p) for p in db.scalars(select(Project).order_by(Project.created_at.desc()))]


@router.get("/api/projects/{pid}")
def get_project(pid: str, db: Session = Depends(get_db)):
    return S.project(db.get(Project, pid) or _404(), with_variants=True)


@router.patch("/api/projects/{pid}")
def patch_project(pid: str, req: ProjectIn, db: Session = Depends(get_db)):
    p = db.get(Project, pid) or _404()
    _validate(req, db)
    p.name, p.product, p.material_ids = req.name, req.product.model_dump(), req.material_ids
    p.platforms, p.options = req.platforms, req.options
    db.commit()
    return S.project(p)


class GenerateReq(BaseModel):
    platforms: list[str] | None = None
    count: int = Field(1, ge=1, le=10)


@router.post("/api/projects/{pid}/generate")
def generate(pid: str, req: GenerateReq, db: Session = Depends(get_db)):
    p = db.get(Project, pid) or _404()
    platforms = req.platforms or p.platforms
    ready = db.scalar(select(Material.id).where(Material.id.in_(p.material_ids or [""]), Material.status == "ready"))
    if not ready:
        raise HTTPException(409, "项目素材尚未分析完成（或未选择素材）")
    try:
        variants = new_variants(db, p, platforms, req.count)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    p.status = "generating"
    for platform in platforms:
        ids = [v.id for v in variants if v.platform == platform]
        queue.enqueue(db, "render", "generate_variants", {"project_id": p.id, "platform": platform, "variant_ids": ids},
                      max_attempts=2, commit=False)
    db.commit()
    return {"project": S.project(p), "variants": [S.variant(v) for v in variants]}


# ---------------------------------------------------------------------- variants
@router.get("/api/variants")
def list_variants(project_id: str | None = None, platform: str | None = None, status: str | None = None,
                  limit: int = 200, db: Session = Depends(get_db)):
    q = select(Variant).order_by(Variant.created_at.desc()).limit(min(limit, 1000))
    if project_id:
        q = q.where(Variant.project_id == project_id)
    if platform:
        q = q.where(Variant.platform == platform)
    if status:
        q = q.where(Variant.status.in_(status.split(",")))
    return [S.variant(v) for v in db.scalars(q)]


@router.get("/api/variants/{vid}")
def get_variant(vid: str, db: Session = Depends(get_db)):
    v = db.get(Variant, vid) or _404()
    d = S.variant(v, full=True)
    r = db.scalar(select(DedupReport).where(DedupReport.variant_id == vid).order_by(DedupReport.created_at.desc()))
    d["dedup_report"] = S.report(r) if r else None
    d["deliveries"] = [S.delivery(x) for x in db.scalars(select(Delivery).where(Delivery.variant_id == vid))]
    return d


class CopyPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    hashtags: list[str] | None = None


@router.patch("/api/variants/{vid}")
def patch_variant(vid: str, req: CopyPatch, db: Session = Depends(get_db)):
    v = db.get(Variant, vid) or _404()
    c = dict(v.copywriting or {})
    for k in ("title", "description", "hashtags"):
        if getattr(req, k) is not None:
            c[k] = getattr(req, k)
    spec = PLATFORM_SPECS[v.platform]
    if len(c.get("title", "")) > spec["title_max"]:
        raise HTTPException(422, f"标题超过 {spec['title_max']} 字")
    v.copywriting = c
    db.commit()
    return S.variant(v)


class ReviewReq(BaseModel):
    note: str = ""


@router.post("/api/variants/{vid}/approve")
def approve(vid: str, req: ReviewReq, db: Session = Depends(get_db), who: str = Depends(require_api_key)):
    v = db.get(Variant, vid) or _404()
    if v.status not in ("passed", "review"):
        raise HTTPException(409, f"状态 {v.status} 的成片不能批准（被拒绝的请重新生成）")
    v.status, v.approved_by, v.approved_at = "approved", who, utcnow()
    _record_review(db, vid, who, "approve", req.note)
    db.commit()
    return S.variant(v)


@router.post("/api/variants/{vid}/discard")
def discard(vid: str, req: ReviewReq, db: Session = Depends(get_db), who: str = Depends(require_api_key)):
    v = db.get(Variant, vid) or _404()
    if db.scalar(select(Delivery.id).where(Delivery.variant_id == vid, Delivery.state.notin_(("failed", "canceled")))):
        raise HTTPException(409, "成片已在发布流程中，不能作废")
    v.status = "discarded"  # discarded variants no longer count as history for dedup
    _record_review(db, vid, who, "discard", req.note)
    db.commit()
    return S.variant(v)


@router.post("/api/variants/{vid}/recheck")
def recheck(vid: str, db: Session = Depends(get_db)):
    v = db.get(Variant, vid) or _404()
    if not v.file_path:
        raise HTTPException(409, "成片尚未渲染")
    queue.enqueue(db, "dedup", "recheck_variant", {"variant_id": vid}, dedupe_key=f"recheck:{vid}")
    return {"queued": True}


def _record_review(db: Session, vid: str, who: str, decision: str, note: str) -> None:
    r = db.scalar(select(DedupReport).where(DedupReport.variant_id == vid).order_by(DedupReport.created_at.desc()))
    if r:
        r.reviewed_by, r.review_decision, r.review_note, r.reviewed_at = who, decision, note, utcnow()


@router.delete("/api/variants/{vid}")
def delete_variant(vid: str, db: Session = Depends(get_db)):
    v = db.get(Variant, vid) or _404()
    if db.scalar(select(Delivery.id).where(Delivery.variant_id == vid)):
        raise HTTPException(409, "已有发布记录的成片不能删除（保留审计与查重历史）")
    s = get_settings()
    for rel in (v.file_path, v.cover_path):
        if rel:
            s.abs(rel).unlink(missing_ok=True)
    db.execute(delete(Fingerprint).where(Fingerprint.owner_type == "variant", Fingerprint.owner_id == vid))
    db.delete(v)
    db.commit()
    return {"deleted": vid}


# ---------------------------------------------------------------------- dedup reports
@router.get("/api/dedup/reports")
def list_reports(decision: str | None = None, limit: int = 100, db: Session = Depends(get_db)):
    q = select(DedupReport).order_by(DedupReport.created_at.desc()).limit(min(limit, 500))
    if decision:
        q = q.where(DedupReport.decision == decision)
    out = []
    for r in db.scalars(q):
        d = S.report(r)
        v = db.get(Variant, r.variant_id)
        d["variant"] = S.variant(v) if v else None
        out.append(d)
    return out


def _404():
    raise HTTPException(404, "not found")
