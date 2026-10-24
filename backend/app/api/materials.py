"""素材 / BGM / 历史作品 ingestion endpoints."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import PLATFORM_SPECS, get_settings
from ..creative import vision
from ..db import get_db
from ..jobs import queue
from ..models import BgmTrack, Fingerprint, HistoryItem, Material, Variant
from ..services.ingest import VIDEO_EXT, locate_path, sha256_file, store_upload
from . import serializers as S

router = APIRouter(tags=["素材"])
LICENSES = ("self_shot", "purchased", "authorized", "unknown")


async def _save_upload(file: UploadFile) -> Path:
    """Stream to a temp file under data/tmp, enforcing the size limit (never holds the file in memory)."""
    limit = get_settings().max_upload_mb * 1048576
    tmp = Path(tempfile.mkstemp(dir=get_settings().path("tmp"))[1])
    size = 0
    with open(tmp, "wb") as out:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > limit:
                out.close()
                tmp.unlink(missing_ok=True)
                raise HTTPException(413, f"文件超过 {get_settings().max_upload_mb}MB 上限")
            out.write(chunk)
    return tmp


def _new_material(db: Session, path: Path, filename: str, product: str, license_type: str, stored_rel: str) -> Material:
    digest = sha256_file(path)
    if db.scalar(select(Material).where(Material.sha256 == digest)):
        raise HTTPException(409, f"素材已存在：{filename}")
    m = Material(filename=filename, storage_path=stored_rel, sha256=digest, size_bytes=path.stat().st_size,
                 product=product, license_type=license_type)
    db.add(m)
    db.flush()
    queue.enqueue(db, "analyze", "analyze_material", {"material_id": m.id}, dedupe_key=f"mat:{m.id}", commit=False)
    return m


@router.post("/api/materials/upload")
async def upload_material(file: UploadFile, product: str = Form(""), license_type: str = Form("self_shot"),
                          db: Session = Depends(get_db)):
    if license_type not in LICENSES:
        raise HTTPException(422, f"license_type must be one of {LICENSES}")
    tmp = await _save_upload(file)
    try:
        dst = store_upload(tmp, file.filename or "upload.mp4", "materials")
    except ValueError as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(415, str(e)) from e
    try:
        m = _new_material(db, dst, Path(file.filename or dst.name).name, product, license_type, get_settings().rel(dst))
    except HTTPException:
        dst.unlink(missing_ok=True)
        raise
    db.commit()
    return S.material(m)


class ImportReq(BaseModel):
    paths: list[str] = Field(min_length=1)
    product: str = ""
    product_from_folder: bool = True
    license_type: str = "self_shot"


@router.post("/api/materials/import")
def import_materials(req: ImportReq, db: Session = Depends(get_db)):
    """Register files that already sit on the server (NAS / camera dumps) without copying. Only IMPORT_ROOTS."""
    roots = [locate_path(r).resolve() for r in get_settings().import_root_paths]
    if not roots:
        raise HTTPException(403, "server-side import is disabled (set IMPORT_ROOTS)")
    files: list[Path] = []
    for raw in req.paths:
        p = locate_path(Path(raw).expanduser()).resolve()
        if not any(p.is_relative_to(r) for r in roots):
            raise HTTPException(403, f"{raw} is outside IMPORT_ROOTS")
        if p.is_dir():
            files += sorted(x for x in p.rglob("*") if x.is_file() and x.suffix.lower() in VIDEO_EXT)
        elif p.is_file():
            files.append(p)
    created, skipped = [], []
    for f in files:
        product = req.product or (f.parent.name if req.product_from_folder else "")
        try:
            m = _new_material(db, f, f.name, product, req.license_type, str(f))
            created.append(S.material(m))
        except HTTPException as e:
            skipped.append({"path": str(f), "reason": e.detail})
    db.commit()
    return {"created": created, "skipped": skipped}


@router.get("/api/materials")
def list_materials(status: str | None = None, product: str | None = None, db: Session = Depends(get_db)):
    q = select(Material).order_by(Material.created_at.desc())
    if status:
        q = q.where(Material.status == status)
    if product:
        q = q.where(Material.product == product)
    return [S.material(m) for m in db.scalars(q)]


@router.get("/api/materials/{mid}")
def get_material(mid: str, db: Session = Depends(get_db)):
    m = db.get(Material, mid) or _404()
    d = S.material(m, shots=True)
    used = [v for v in db.scalars(select(Variant)) if any(c.get("material_id") == mid for c in (v.plan or {}).get("clips", []))]
    d["used_in_variants"] = len(used)
    return d


class MaterialPatch(BaseModel):
    product: str | None = None
    tags: list[str] | None = None
    license_type: str | None = None
    license_note: str | None = None
    blacklisted: bool | None = None


@router.patch("/api/materials/{mid}")
def patch_material(mid: str, req: MaterialPatch, db: Session = Depends(get_db)):
    m = db.get(Material, mid) or _404()
    if req.license_type is not None:
        if req.license_type not in LICENSES:
            raise HTTPException(422, f"license_type must be one of {LICENSES}")
        m.license_type = req.license_type
    for k in ("product", "tags", "license_note"):
        if getattr(req, k) is not None:
            setattr(m, k, getattr(req, k))
    if req.blacklisted is not None:
        m.status = "blacklisted" if req.blacklisted else ("ready" if m.proxy_path else "uploaded")
    db.commit()
    return S.material(m)


@router.post("/api/materials/{mid}/reanalyze")
def reanalyze(mid: str, db: Session = Depends(get_db)):
    m = db.get(Material, mid) or _404()
    m.status = "uploaded"
    queue.enqueue(db, "analyze", "analyze_material", {"material_id": m.id}, dedupe_key=f"mat:{m.id}", commit=False)
    db.commit()
    return S.material(m)


class DescribeReq(BaseModel):
    material_ids: list[str] | None = None  # omitted = every ready material without current notes
    force: bool = False


@router.post("/api/materials/describe")
def describe_materials(req: DescribeReq, db: Session = Depends(get_db)):
    """Queue vision shot notes (used to ground copy). Backfills materials analysed before notes existed."""
    if not vision.configured():
        raise HTTPException(409, "视觉模型未配置：请设置 AGENT_VISION_MODEL_*")
    q = select(Material).where(Material.status == "ready")
    if req.material_ids is not None:
        q = q.where(Material.id.in_(req.material_ids or [""]))
    queued = []
    for m in db.scalars(q):
        if req.force or not vision.current(m.analysis) or vision.current(m.analysis).get("errors"):
            if queue.enqueue(db, "analyze", "describe_material", {"material_id": m.id, "force": req.force},
                             dedupe_key=f"vision:{m.id}", max_attempts=3, commit=False):
                queued.append(m.id)
    db.commit()
    return {"queued": queued}


@router.delete("/api/materials/{mid}")
def delete_material(mid: str, db: Session = Depends(get_db)):
    m = db.get(Material, mid) or _404()
    used = any(any(c.get("material_id") == mid for c in (v.plan or {}).get("clips", [])) for v in db.scalars(select(Variant)))
    if used:  # keep provenance intact for dedup: an already-used material can only be blacklisted
        raise HTTPException(409, "素材已被成片使用，不能删除（查重依赖其来源记录），可以拉黑")
    s = get_settings()
    for rel in (m.proxy_path, m.thumb_path):
        if rel:
            s.abs(rel).unlink(missing_ok=True)
    if not Path(m.storage_path).is_absolute():  # uploaded copy (server-side imports are left untouched)
        s.abs(m.storage_path).unlink(missing_ok=True)
    db.execute(delete(Fingerprint).where(Fingerprint.owner_type == "material", Fingerprint.owner_id == mid))
    db.delete(m)
    db.commit()
    return {"deleted": mid}


# ---------------------------------------------------------------------- BGM
@router.post("/api/bgm/upload")
async def upload_bgm(file: UploadFile, name: str = Form(""), mood: str = Form("upbeat"),
                     license_note: str = Form(...), db: Session = Depends(get_db)):
    if not license_note.strip():
        raise HTTPException(422, "BGM 必须填写授权说明")
    tmp = await _save_upload(file)
    try:
        dst = store_upload(tmp, file.filename or "bgm.mp3", "bgm")
    except ValueError as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(415, str(e)) from e
    t = BgmTrack(name=name or Path(file.filename or dst.name).stem, storage_path=get_settings().rel(dst), mood=mood,
                 license_note=license_note)
    db.add(t)
    db.flush()
    queue.enqueue(db, "analyze", "analyze_bgm", {"bgm_id": t.id}, commit=False)
    db.commit()
    return {"id": t.id, "name": t.name}


@router.get("/api/bgm")
def list_bgm(db: Session = Depends(get_db)):
    return [{"id": t.id, "name": t.name, "duration": t.duration, "bpm": t.bpm, "beats": len(t.beats or []),
             "mood": t.mood, "license_note": t.license_note, "active": t.active}
            for t in db.scalars(select(BgmTrack).order_by(BgmTrack.created_at.desc()))]


@router.patch("/api/bgm/{bid}")
def patch_bgm(bid: str, active: bool, db: Session = Depends(get_db)):
    t = db.get(BgmTrack, bid) or _404()
    t.active = active
    db.commit()
    return {"id": t.id, "active": t.active}


# ---------------------------------------------------------------------- history library
@router.post("/api/history/upload")
async def upload_history(file: UploadFile, title: str = Form(""), platform: str = Form(""),
                         db: Session = Depends(get_db)):
    if platform and platform not in PLATFORM_SPECS:
        raise HTTPException(422, f"platform must be one of {list(PLATFORM_SPECS)} or empty")
    tmp = await _save_upload(file)
    try:
        dst = store_upload(tmp, file.filename or "history.mp4", "history")
    except ValueError as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(415, str(e)) from e
    digest = sha256_file(dst)
    if db.scalar(select(HistoryItem).where(HistoryItem.sha256 == digest)):
        dst.unlink(missing_ok=True)
        raise HTTPException(409, "该历史作品已导入")
    h = HistoryItem(title=title or Path(file.filename or "").stem, platform=platform,
                    storage_path=get_settings().rel(dst), sha256=digest)
    db.add(h)
    db.flush()
    queue.enqueue(db, "analyze", "analyze_history", {"history_id": h.id}, commit=False)
    db.commit()
    return S.history(h)


@router.get("/api/history")
def list_history(db: Session = Depends(get_db)):
    return [S.history(h) for h in db.scalars(select(HistoryItem).order_by(HistoryItem.created_at.desc()))]


@router.delete("/api/history/{hid}")
def delete_history(hid: str, db: Session = Depends(get_db)):
    h = db.get(HistoryItem, hid) or _404()
    get_settings().abs(h.storage_path).unlink(missing_ok=True)
    db.execute(delete(Fingerprint).where(Fingerprint.owner_type == "history", Fingerprint.owner_id == hid))
    db.delete(h)
    db.commit()
    return {"deleted": hid}


def _404():
    raise HTTPException(404, "not found")
