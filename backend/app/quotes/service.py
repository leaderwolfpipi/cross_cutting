"""Prepare reviewed quote videos for the existing multi-platform publisher."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import PLATFORM_SPECS, get_settings
from ..db import utcnow
from ..models import Account, Delivery, Project, PublishBatch, QuoteSheet, Variant
from ..services.ingest import sha256_file
from .parser import ambiguous_ready_pairs, categories
from .render import export_category, paginate

ZONE = ZoneInfo("Asia/Shanghai")


def today() -> str:
    return datetime.now(ZONE).date().isoformat()


def revision(sheet: QuoteSheet) -> str:
    payload = sheet.source_text + repr(sheet.entries) + repr(sheet.corrections) + str(sheet.confirmed_at)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def category_folder(sheet: QuoteSheet, category: str) -> Path:
    names = [c["name"] for c in categories(sheet.entries)]
    if category not in names:
        raise ValueError(f"品类不存在：{category}")
    index = names.index(category)
    return get_settings().path("quotes", sheet.id, revision(sheet), f"category_{index:02d}")


def copy_for(sheet: QuoteSheet, category: str, platform: str) -> dict:
    short_date = sheet.quote_date[5:].replace("-", ".")
    base = (f"{sheet.supplier}｜{sheet.quote_date} {category}全规格报价（{sheet.unit}）。\n"
            "逐页展示全部规格，暂停截图或保存长图。供应商报价参考，具体交易条件请确认。")
    titles = {"douyin": f"{category}全规格报价｜{short_date}",
              "kuaishou": f"{category}全规格报价｜{short_date}",
              "channels": f"{category}报价 {short_date}",
              "xiaohongshu": f"{category}全规格价目表"}
    tags = {"douyin": ["海鲜报价", category.replace("/", "")],
            "kuaishou": ["海鲜批发", "全规格报价"],
            "channels": [], "xiaohongshu": ["海鲜价目表", "海鲜批发"]}
    spec = PLATFORM_SPECS[platform]
    return {"title": titles[platform][:spec["title_max"]], "description": base,
            "hashtags": tags[platform][:spec["tags_max"]]}


def sheet_error(sheet: QuoteSheet, selected_categories: list[str]) -> str:
    if sheet.quote_date != today():
        return f"报价日期 {sheet.quote_date} 已过期；当前日期 {today()}"
    if not sheet.confirmed_by or not sheet.confirmed_at:
        return "报价单尚未由操作人确认"
    if any(e["status"] != "ready" for e in sheet.entries):
        return "存在待复核的报价行"
    if any(not c["acknowledged"] for c in sheet.corrections):
        return "存在未说明的更正记录"
    if ambiguous_ready_pairs(sheet.entries):
        return "重叠规格缺少可展示的批次或状态"
    available = {c["name"] for c in categories(sheet.entries)}
    if not selected_categories or any(c not in available for c in selected_categories):
        return "请选择报价单中的有效品类"
    return ""


def mixed_quote(db: Session, sheet_id: str, entry_id: str, expected_revision: str = "") -> dict:
    """Return a verified price for one product, never a free-text project price."""
    sheet = db.get(QuoteSheet, sheet_id)
    if sheet is None:
        raise ValueError("关联的报价单不存在")
    db.refresh(sheet)
    entry = next((e for e in sheet.entries if e.get("id") == entry_id), None)
    if entry is None:
        raise ValueError("关联的报价行不存在")
    error = sheet_error(sheet, [entry["category"]])
    if error:
        raise ValueError(error)
    current_revision = revision(sheet)
    if expected_revision and current_revision != expected_revision:
        raise ValueError("报价单已变动，请重新关联并生成混剪")
    if entry.get("qualifier"):
        raise ValueError("含附加报价的规格暂不能用于混剪，请选单一报价行")
    return {"sheet_id": sheet.id, "revision": current_revision, "entry_id": entry_id,
            "quote_date": sheet.quote_date, "category": entry["category"], "product": entry["product"],
            "spec": entry["spec"], "price": entry["price"], "unit": sheet.unit, "supplier": sheet.supplier}


def target_plan(db: Session, sheet: QuoteSheet, selected_categories: list[str], account_ids: list[str]) -> dict:
    error = sheet_error(sheet, selected_categories)
    accounts = list(db.scalars(select(Account).where(Account.id.in_(account_ids or [""]))))
    if not account_ids or len(accounts) != len(set(account_ids)):
        error = error or "请选择至少一个有效平台账号"
    checks = []
    now = datetime.now(ZONE)
    end = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    for acc in accounts:
        reasons = []
        if acc.status != "active":
            reasons.append(f"账号状态为 {acc.status}")
        if acc.platform not in PLATFORM_SPECS:
            reasons.append("未知平台")
        if acc.mode == "api":
            from ..publish import registry

            if not registry.supports_api(acc.platform) or not registry.adapter(acc.platform).configured():
                reasons.append("开放平台应用凭证未配置")
            if not acc.access_token_enc and not acc.refresh_token_enc:
                reasons.append("账号尚未授权")
        gap = acc.min_interval_s if acc.min_interval_s >= 0 else get_settings().account_min_interval_s
        earliest = now
        if acc.last_published_at:
            last = acc.last_published_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZONE)
            earliest = max(now, last + timedelta(seconds=gap))
        if earliest + timedelta(seconds=max(0, len(selected_categories) - 1) * gap) >= end:
            reasons.append("该账号按发布间隔排队后会跨过报价日期")
        from ..publish.orchestrator import account_quota

        used, limit = account_quota(db, acc)
        if used + len(selected_categories) > limit:
            reasons.append(f"近24小时配额不足：已用 {used}/{limit}，本次需 {len(selected_categories)}")
        earlier = db.execute(select(Delivery, Variant).join(Variant, Delivery.variant_id == Variant.id).where(
            Delivery.account_id == acc.id, Delivery.state.notin_(("failed", "canceled")))).all()
        duplicate_categories = [category for category in selected_categories if any(
            (v.plan or {}).get("quote", {}).get("sheet_id") == sheet.id and
            (v.plan or {}).get("quote", {}).get("quote_date") == sheet.quote_date and
            (v.plan or {}).get("quote", {}).get("category") == category for _, v in earlier)]
        if duplicate_categories:
            reasons.append("这些品类已发往该账号或正在排队：" + "、".join(duplicate_categories))
        checks.append({"account_id": acc.id, "platform": acc.platform, "account": acc.display_name,
                       "mode": acc.mode, "targets": len(selected_categories), "ok": not reasons, "reasons": reasons})
    if not accounts:
        error = error or "没有可用账号"
    return {"ok": not error and all(c["ok"] for c in checks), "error": error,
            "categories": selected_categories, "targets": checks,
            "total": len(selected_categories) * len(accounts)}


def carries_quote(plan: dict | None) -> bool:
    """True when the video shows prices: a quote card in the edit or today's quote slideshow at the end."""
    return bool((plan or {}).get("quote") or (plan or {}).get("quote_outro"))


def _validate_outro(db: Session, outro: dict) -> str:
    sheet = db.get(QuoteSheet, outro.get("sheet_id"))
    if not sheet:
        return "片尾报价单不存在"
    db.refresh(sheet)
    error = sheet_error(sheet, [c["name"] for c in categories(sheet.entries)])
    if error:
        return f"片尾报价：{error}"
    if revision(sheet) != outro.get("revision") or sheet.quote_date != outro.get("quote_date"):
        return "片尾报价单已变动，请重新生成"
    return ""


def validate_variant(db: Session, variant: Variant) -> str:
    plan = variant.plan or {}
    if not carries_quote(plan):
        return "报价成片缺少报价快照"
    if plan.get("quote_outro"):
        error = _validate_outro(db, plan["quote_outro"])
        if error or not plan.get("quote"):
            return error
    quote = plan["quote"]
    sheet = db.get(QuoteSheet, quote.get("sheet_id"))
    if not sheet:
        return "原报价单不存在"
    db.refresh(sheet)
    error = sheet_error(sheet, [quote.get("category", "")])
    if error:
        return error
    if revision(sheet) != quote.get("revision"):
        return "报价单已变动，请重新生成报价视频"
    if quote.get("quote_date") != sheet.quote_date:
        return "成片报价日期与当前报价单不一致"
    if quote.get("entry_id"):
        entry = next((e for e in sheet.entries if e.get("id") == quote["entry_id"]), None)
        if not entry or any(entry.get(key) != quote.get(key) for key in ("product", "spec", "price")):
            return "成片报价与当前报价行不一致"
    return ""


def media_allowed(db: Session, rel: str) -> bool:
    """Revoke rendered quote assets when the source changes or the date ends."""
    parts = Path(rel).parts
    mixed = db.scalar(select(Variant).where((Variant.file_path == rel) | (Variant.cover_path == rel)))
    if mixed and carries_quote(mixed.plan):
        return not validate_variant(db, mixed)
    if len(parts) < 5 or parts[0] != "quotes":
        return True
    if parts[-1] in ("preview.png", "preview_history.png"):
        return True
    sheet = db.get(QuoteSheet, parts[1])
    return bool(sheet and sheet.quote_date == today() and sheet.confirmed_by and revision(sheet) == parts[2])


def prepare_and_publish(db: Session, sheet_id: str, expected_revision: str, selected_categories: list[str],
                        account_ids: list[str], requested_by: str, progress) -> dict:
    """Idempotent render + Variant creation, then the normal one-click state machine."""
    sheet = db.get(QuoteSheet, sheet_id)
    if sheet:
        db.refresh(sheet)
    if not sheet or revision(sheet) != expected_revision:
        raise ValueError("报价单已改变，请重新预检")
    selection = hashlib.sha256(repr((selected_categories, sorted(account_ids))).encode()).hexdigest()[:12]
    project = next((p for p in db.scalars(select(Project).order_by(Project.created_at.desc()))
                    if (p.options or {}).get("quote_sheet_id") == sheet_id and
                    (p.options or {}).get("quote_revision") == expected_revision and
                    (p.options or {}).get("quote_selection") == selection), None)
    if project is not None:
        existing = db.scalar(select(PublishBatch).where(PublishBatch.project_id == project.id,
                                                         PublishBatch.status != "preflight_failed")
                             .order_by(PublishBatch.created_at.desc()))
        if existing:
            return {"project_id": project.id, "batch_id": existing.id, "reused": True}
    report = target_plan(db, sheet, selected_categories, account_ids)
    if not report["ok"]:
        raise ValueError(report["error"] or "; ".join(r for t in report["targets"] for r in t["reasons"]))
    if project is None:
        project = Project(name=f"{sheet.quote_date} 全规格报价系列", product={"name": "全规格报价"},
                          material_ids=[], platforms=list({a["platform"] for a in report["targets"]}),
                          options={"quote_sheet_id": sheet_id, "quote_revision": expected_revision,
                                   "quote_selection": selection}, status="generating")
        db.add(project)
        db.commit()
    for i, category in enumerate(selected_categories):
        db.refresh(sheet)
        if sheet.quote_date != today() or revision(sheet) != expected_revision:
            raise ValueError("渲染期间报价已过期或变动，发布已停止")
        folder = category_folder(sheet, category)
        if not (folder / "all_specs.mp4").is_file() or not (folder / "cover.jpg").is_file():
            export_category(sheet, category, folder)
        video, cover = folder / "all_specs.mp4", folder / "cover.jpg"
        for platform in {a["platform"] for a in report["targets"]}:
            # A project may hold several categories on the same platform, so locate by quote metadata.
            existing_variant = next((v for v in db.scalars(select(Variant).where(
                Variant.project_id == project.id, Variant.platform == platform, Variant.template_id == "quote_series"))
                if (v.plan or {}).get("quote", {}).get("category") == category), None)
            if existing_variant:
                continue
            copy = copy_for(sheet, category, platform)
            v = Variant(project_id=project.id, platform=platform, seq=i + 1, template_id="quote_series",
                        plan={"quote": {"sheet_id": sheet.id, "revision": expected_revision,
                                        "quote_date": sheet.quote_date, "category": category, "copy": copy},
                              "texts": [], "cover": {"time": 0.5}},
                        copywriting=copy, plan_report={"source": "confirmed_quote_sheet"},
                        render_backend="quote_cards", file_path=get_settings().rel(video),
                        cover_path=get_settings().rel(cover), sha256=sha256_file(video),
                        size_bytes=video.stat().st_size, duration=5 * len(paginate(sheet.entries, category)),
                        width=1080, height=1920, status="approved", dedup_decision="pass",
                        approved_by=sheet.confirmed_by, approved_at=sheet.confirmed_at)
            db.add(v)
        db.commit()
        progress((i + 1) / (len(selected_categories) + 1), f"已准备 {category}")
    project.status = "ready"
    db.commit()
    db.refresh(sheet)
    if revision(sheet) != expected_revision or sheet.quote_date != today():
        raise ValueError("发布前报价已过期或变动")
    from ..publish import orchestrator

    targets = []
    for category in selected_categories:
        for account in report["targets"]:
            v = next((v for v in db.scalars(select(Variant).where(
                Variant.project_id == project.id, Variant.platform == account["platform"],
                Variant.template_id == "quote_series")) if v.plan["quote"]["category"] == category), None)
            if v:
                targets.append({"variant_id": v.id, "account_id": account["account_id"]})
    if len(targets) != report["total"]:
        raise ValueError("报价成片准备数量不完整")
    batch = orchestrator.one_click(db, targets, requested_by, project.id)
    progress(1.0, f"发布批次 {batch.id} 已创建")
    return {"project_id": project.id, "batch_id": batch.id, "status": batch.status,
            "targets": len(targets)}
