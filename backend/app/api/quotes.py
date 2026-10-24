"""Supplier quote sheets and the separate all-specification video series."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db, utcnow
from ..jobs import queue
from ..models import Account, QuoteSheet, Variant
from ..quotes.parser import _range, ambiguous_ready_pairs, categories, parse_sheet
from ..quotes.render import export_category, paginate, render_preview
from ..quotes.source import load_quote_text
from ..quotes import service as quote_service
from ..security import require_api_key, signed_media_url

router = APIRouter(prefix="/api/quotes", tags=["报价系列"])
SAMPLE = Path(__file__).resolve().parents[1] / "quotes" / "seed_2026-10-22.txt"


def today() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def _get(db: Session, sid: str) -> QuoteSheet:
    sheet = db.get(QuoteSheet, sid)
    if sheet is None:
        raise HTTPException(404, "报价单不存在")
    return sheet


def _revision(sheet: QuoteSheet) -> str:
    # Asset paths must use the same revision as media_allowed and publish validation.
    return quote_service.revision(sheet)


def _base(sheet: QuoteSheet) -> Path:
    return get_settings().path("quotes", sheet.id, _revision(sheet))


def _safe_category(sheet: QuoteSheet, category: str) -> tuple[int, dict]:
    found = next(((i, c) for i, c in enumerate(categories(sheet.entries)) if c["name"] == category), None)
    if found is None:
        raise HTTPException(404, "品类不存在")
    return found


def _summary(sheet: QuoteSheet, full: bool = False) -> dict:
    cats = categories(sheet.entries)
    result = {"id": sheet.id, "supplier": sheet.supplier, "quote_date": sheet.quote_date, "unit": sheet.unit,
              "categories": cats, "count": len(sheet.entries), "review_count": sum(c["review_count"] for c in cats) + sum(not c["acknowledged"] for c in sheet.corrections),
              "confirmed_by": sheet.confirmed_by, "confirmed_at": sheet.confirmed_at,
              "current": sheet.quote_date == today(), "created_at": sheet.created_at,
              "review_note": sheet.review_note}
    if full:
        result.update(source_text=sheet.source_text, entries=sheet.entries, corrections=sheet.corrections)
    return result


def _clear_unpublished_assets(db: Session, sheet: QuoteSheet) -> None:
    """Keep files referenced by audit/publish records; media access is revoked by revision checks."""
    in_use = any((v.plan or {}).get("quote", {}).get("sheet_id") == sheet.id for v in db.scalars(
        select(Variant).where(Variant.template_id == "quote_series")))
    if not in_use:
        shutil.rmtree(get_settings().path("quotes", sheet.id), ignore_errors=True)


class ImportIn(BaseModel):
    source_text: str = Field(min_length=20, max_length=300000)


@router.post("")
def import_sheet(body: ImportIn, db: Session = Depends(get_db)):
    try:
        parsed = parse_sheet(body.source_text)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not parsed["entries"]:
        raise HTTPException(422, "没有解析出规格与价格，请检查报价原文")
    digest = hashlib.sha256(body.source_text.strip().encode()).hexdigest()
    for existing in db.scalars(select(QuoteSheet)):
        if hashlib.sha256(existing.source_text.strip().encode()).hexdigest() == digest:
            return _summary(existing, True)
    sheet = QuoteSheet(source_text=body.source_text, **parsed)
    db.add(sheet)
    db.commit()
    return _summary(sheet, True)


@router.post("/import-sample")
def import_sample(db: Session = Depends(get_db)):
    return import_sheet(ImportIn(source_text=SAMPLE.read_text()), db)


@router.post("/import-latest")
def import_latest(db: Session = Depends(get_db)):
    try:
        text = load_quote_text()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return import_sheet(ImportIn(source_text=text), db)


@router.get("")
def list_sheets(db: Session = Depends(get_db)):
    return [_summary(s) for s in db.scalars(select(QuoteSheet).order_by(QuoteSheet.created_at.desc()))]


@router.get("/{sid}")
def get_sheet(sid: str, db: Session = Depends(get_db)):
    return _summary(_get(db, sid), True)


class EntryEdit(BaseModel):
    spec: str = Field(min_length=1, max_length=80)
    price: float = Field(gt=0, le=1000000)
    qualifier: str = Field(default="", max_length=100)
    status: str = Field(pattern="^(ready|review)$")
    review_note: str = Field(default="", max_length=500)


@router.patch("/{sid}/entries/{eid}")
def edit_entry(sid: str, eid: str, body: EntryEdit, db: Session = Depends(get_db)):
    sheet = _get(db, sid)
    rows = [dict(e) for e in sheet.entries]
    row = next((e for e in rows if e["id"] == eid), None)
    if row is None:
        raise HTTPException(404, "报价行不存在")
    if body.status == "ready" and (row["status"] == "review" or body.spec != row["spec"] or body.price != row["price"]) and not body.review_note.strip():
        raise HTTPException(422, "复核或修改报价时必须填写依据")
    interval = _range(body.spec)
    peers = [other for other in rows if other["id"] != eid and other["product"] == row["product"] and
             (other["spec"] == body.spec or (interval and (other_interval := _range(other["spec"])) and
              max(interval[0], other_interval[0]) < min(interval[1], other_interval[1]))) and other["price"] != body.price]
    if body.status == "ready" and peers and (not body.qualifier.strip() or any(
            other["status"] == "ready" and other.get("qualifier", "") == body.qualifier for other in peers)):
        raise HTTPException(422, "重叠规格有不同价格，请在附加报价填写可展示的批次或状态，以区分价格")
    row.update(body.model_dump())
    sheet.entries = rows
    sheet.confirmed_by = ""
    sheet.confirmed_at = None
    sheet.updated_at = utcnow()
    _clear_unpublished_assets(db, sheet)
    db.commit()
    return _summary(sheet, True)


class CorrectionReview(BaseModel):
    review_note: str = Field(min_length=4, max_length=500)


@router.post("/{sid}/corrections/{cid}/acknowledge")
def acknowledge_correction(sid: str, cid: str, body: CorrectionReview, db: Session = Depends(get_db)):
    sheet = _get(db, sid)
    records = [dict(c) for c in sheet.corrections]
    record = next((c for c in records if c["id"] == cid), None)
    if record is None:
        raise HTTPException(404, "更正记录不存在")
    record["acknowledged"] = True
    record["review_note"] = body.review_note.strip()
    sheet.corrections = records
    sheet.confirmed_by = ""
    sheet.confirmed_at = None
    sheet.updated_at = utcnow()
    _clear_unpublished_assets(db, sheet)
    db.commit()
    return _summary(sheet, True)


class ConfirmIn(BaseModel):
    operator: str = Field(min_length=1, max_length=100)
    note: str = Field(min_length=4, max_length=1000)


@router.post("/{sid}/confirm")
def confirm_sheet(sid: str, body: ConfirmIn, db: Session = Depends(get_db)):
    sheet = _get(db, sid)
    if sheet.quote_date != today():
        raise HTTPException(409, f"报价日期为 {sheet.quote_date}，当前为 {today()}；请导入当天报价后再确认导出")
    if any(e["status"] != "ready" for e in sheet.entries) or any(not c["acknowledged"] for c in sheet.corrections):
        raise HTTPException(409, "存在待复核的规格或无法匹配的更正记录")
    if ambiguous_ready_pairs(sheet.entries):
        raise HTTPException(409, "重叠规格的不同报价仍缺少可展示的批次或状态区分")
    sheet.confirmed_by = body.operator.strip()
    sheet.review_note = body.note.strip()
    sheet.confirmed_at = utcnow()
    db.commit()
    return _summary(sheet)


@router.get("/{sid}/categories/{category}/preview")
def preview(sid: str, category: str, db: Session = Depends(get_db)):
    sheet = _get(db, sid)
    index, info = _safe_category(sheet, category)
    pages = paginate(sheet.entries, category)
    # Use a separate cache after the quote date so the historical badge is rendered.
    path = _base(sheet) / f"category_{index:02d}" / (
        "preview.png" if sheet.quote_date == today() else "preview_history.png")
    if not path.exists():
        render_preview(sheet, category, path)
    return {"category": category, "pages": len(pages), "image_url": signed_media_url(get_settings().rel(path)),
            "review_count": info["review_count"], "rows": [e for e in sheet.entries if e["category"] == category]}


@router.post("/{sid}/categories/{category}/export")
def export(sid: str, category: str, db: Session = Depends(get_db)):
    sheet = _get(db, sid)
    index, info = _safe_category(sheet, category)
    if sheet.quote_date != today():
        raise HTTPException(409, f"{sheet.quote_date} 为历史报价，不能作为当天报价导出")
    if not sheet.confirmed_by or not sheet.confirmed_at:
        raise HTTPException(409, "请先完成整张报价单的人工确认")
    if info["review_count"]:
        raise HTTPException(409, "该品类仍有待复核报价")
    folder = _base(sheet) / f"category_{index:02d}"
    try:
        if not (folder / "all_specs.mp4").exists() or not (folder / "cover.jpg").exists():
            files = export_category(sheet, category, folder)
        else:
            files = {"video": folder / "all_specs.mp4", "image": folder / "all_specs.png",
                     "cover": folder / "cover.jpg", "pages": len(paginate(sheet.entries, category))}
    except (RuntimeError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(500, f"导出失败：{exc}") from exc
    short_date = sheet.quote_date[5:].replace("-", ".")
    caption = f"{sheet.supplier}｜{sheet.quote_date} {category}全规格报价（{sheet.unit}）。\n逐页展示全部规格，暂停截图或保存长图。供应商报价参考，具体交易条件请确认。"
    copy = {
        "douyin": {"title": f"{category}全规格报价｜{short_date}", "caption": caption + f"\n#海鲜报价 #{category.replace('/', '')}"},
        "kuaishou": {"title": f"{category}全规格报价｜{short_date}", "caption": caption + "\n#海鲜批发 #全规格报价"},
        "channels": {"title": f"{category}报价 {short_date}"[:16], "caption": caption},
        "xiaohongshu": {"title": f"{category}全规格价目表"[:20], "caption": caption + "\n#海鲜价目表 #海鲜批发"},
    }
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    ttl = max(1, int(((now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0) - now).total_seconds()))
    return {"title": copy["douyin"]["title"], "caption": caption, "platform_copy": copy, "pages": files["pages"],
            "video_url": signed_media_url(get_settings().rel(files["video"]), ttl),
            "image_url": signed_media_url(get_settings().rel(files["image"]), ttl),
            "cover_url": signed_media_url(get_settings().rel(files["cover"]), ttl)}


class QuotePublishIn(BaseModel):
    categories: list[str] | None = None  # omitted = the complete series
    account_ids: list[str] | None = None  # omitted = every active account


def _publish_selection(db: Session, sheet: QuoteSheet, body: QuotePublishIn) -> tuple[list[str], list[str]]:
    names = [c["name"] for c in categories(sheet.entries)]
    chosen = body.categories if body.categories is not None else names
    if not chosen or len(set(chosen)) != len(chosen) or any(name not in names for name in chosen):
        raise HTTPException(422, "请选择不重复的有效品类")
    ids = body.account_ids if body.account_ids is not None else list(db.scalars(
        select(Account.id).where(Account.status == "active")))
    if not ids or len(set(ids)) != len(ids):
        raise HTTPException(422, "请选择不重复的有效平台账号")
    return chosen, ids


@router.post("/{sid}/publish/preflight")
def publish_preflight(sid: str, body: QuotePublishIn, db: Session = Depends(get_db)):
    sheet = _get(db, sid)
    chosen, ids = _publish_selection(db, sheet, body)
    return quote_service.target_plan(db, sheet, chosen, ids)


@router.post("/{sid}/publish")
def publish_series(sid: str, body: QuotePublishIn, db: Session = Depends(get_db),
                   who: str = Depends(require_api_key)):
    sheet = _get(db, sid)
    chosen, ids = _publish_selection(db, sheet, body)
    report = quote_service.target_plan(db, sheet, chosen, ids)
    if not report["ok"]:
        raise HTTPException(409, {"message": "报价发布预检未通过", "report": report})
    rev = quote_service.revision(sheet)
    dedupe = hashlib.sha256(repr((sid, rev, chosen, sorted(ids))).encode()).hexdigest()[:32]
    job = queue.enqueue(db, "render", "prepare_quote_publish",
                        {"sheet_id": sid, "revision": rev, "categories": chosen,
                         "account_ids": ids, "requested_by": who},
                        dedupe_key=f"quote-publish:{dedupe}", max_attempts=1)
    if job is None:
        raise HTTPException(409, "同一报价系列的发布准备任务正在运行")
    return {"job_id": job.id, "status": job.status, "targets": report["total"], "categories": chosen}
