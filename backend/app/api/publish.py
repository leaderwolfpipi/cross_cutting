"""Accounts (OAuth), one-click publishing, deliveries and the operator hand-off."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import PLATFORM_SPECS, get_settings
from ..db import get_db, utcnow
from ..models import Account, Delivery, OAuthState, Project, PublishBatch, Variant
from ..publish import orchestrator as O
from ..publish import registry
from ..publish.base import PublishError, Tokens
from ..quotes.service import carries_quote
from ..security import random_state, require_api_key, signed_page_token
from . import serializers as S

router = APIRouter(tags=["账号与发布"])
public = APIRouter(tags=["OAuth 回调"])  # no API key: the platform redirects the browser here


# ---------------------------------------------------------------------- accounts
class AccountIn(BaseModel):
    platform: str
    display_name: str = ""
    mode: str = "api"  # api | assisted
    daily_limit: int = 0
    min_interval_s: int = -1
    note: str = ""
    # manual token entry (e.g. tokens obtained in the platform console, or the local mock platform)
    access_token: str = ""
    refresh_token: str = ""
    open_id: str = ""
    expires_in: int = 0


@router.get("/api/accounts")
def list_accounts(db: Session = Depends(get_db)):
    return [S.account(a) for a in db.scalars(select(Account).order_by(Account.platform, Account.created_at))]


def _apply(a: Account, req: AccountIn) -> None:
    if req.platform not in PLATFORM_SPECS:
        raise HTTPException(422, f"unknown platform {req.platform}")
    if req.mode not in ("api", "assisted"):
        raise HTTPException(422, "mode must be api or assisted")
    if req.mode == "api" and not registry.supports_api(req.platform):
        raise HTTPException(422, f"{PLATFORM_SPECS[req.platform]['name']}没有开放发布接口，只能使用 assisted 模式")
    a.platform, a.display_name, a.mode, a.note = req.platform, req.display_name, req.mode, req.note
    a.daily_limit, a.min_interval_s = req.daily_limit, req.min_interval_s
    if req.access_token:
        O.store_tokens(a, Tokens(access_token=req.access_token, refresh_token=req.refresh_token,
                                 expires_in=req.expires_in, open_id=req.open_id))
    elif req.open_id:
        a.open_id = req.open_id


@router.post("/api/accounts")
def create_account(req: AccountIn, db: Session = Depends(get_db)):
    a = Account()
    _apply(a, req)
    db.add(a)
    db.commit()
    return S.account(a)


@router.put("/api/accounts/{aid}")
def update_account(aid: str, req: AccountIn, db: Session = Depends(get_db)):
    a = db.get(Account, aid) or _404()
    _apply(a, req)
    db.commit()
    return S.account(a)


@router.post("/api/accounts/{aid}/status")
def set_account_status(aid: str, status: str, db: Session = Depends(get_db)):
    if status not in ("active", "disabled"):
        raise HTTPException(422, "status must be active or disabled")
    a = db.get(Account, aid) or _404()
    a.status = status
    db.commit()
    return S.account(a)


@router.delete("/api/accounts/{aid}")
def delete_account(aid: str, db: Session = Depends(get_db)):
    a = db.get(Account, aid) or _404()
    if db.scalar(select(Delivery.id).where(Delivery.account_id == aid)):
        a.status, a.access_token_enc, a.refresh_token_enc = "disabled", "", ""
        db.commit()
        return {"disabled": aid, "reason": "账号有发布记录，已停用并清除令牌（保留审计）"}
    db.delete(a)
    db.commit()
    return {"deleted": aid}


def _redirect_uri(platform: str) -> str:
    return f"{get_settings().oauth_redirect_base.rstrip('/')}/oauth/{platform}/callback"


@router.get("/api/accounts/oauth/{platform}/authorize")
def oauth_authorize(platform: str, account_id: str = "", db: Session = Depends(get_db)):
    if not registry.supports_api(platform):
        raise HTTPException(422, "该平台没有开放授权")
    ad = registry.adapter(platform)
    if not ad.configured():
        raise HTTPException(409, f"未配置 {platform} 开放平台应用凭证")
    state = random_state()
    db.add(OAuthState(state=state, platform=platform, account_id=account_id))
    db.commit()
    return {"url": ad.authorize_url(state, _redirect_uri(platform))}


@public.get("/oauth/{platform}/callback", response_class=HTMLResponse)
def oauth_callback(platform: str, code: str = "", state: str = "", db: Session = Depends(get_db)):
    st = db.get(OAuthState, state)
    if st is None or st.platform != platform or st.created_at < utcnow() - timedelta(minutes=15):
        raise HTTPException(400, "授权已过期或无效，请重新发起")
    db.delete(st)
    try:
        tok = registry.adapter(platform).exchange_code(code, _redirect_uri(platform))
    except PublishError as e:
        db.commit()
        return HTMLResponse(f"<h3>授权失败</h3><p>{e}</p>", status_code=400)
    a = (db.get(Account, st.account_id) if st.account_id else None) or db.scalar(
        select(Account).where(Account.platform == platform, Account.open_id == tok.open_id)) or Account(
        platform=platform, mode="api", display_name=f"{PLATFORM_SPECS[platform]['name']}账号")
    O.store_tokens(a, tok)
    db.add(a)
    db.commit()
    return HTMLResponse("<h3>授权成功</h3><p>可以关闭此页面，回到管理台。</p><script>setTimeout(()=>window.close(),1500)</script>")


# ---------------------------------------------------------------------- publishing
class Target(BaseModel):
    variant_id: str
    account_id: str
    title: str | None = None
    description: str | None = None
    hashtags: list[str] | None = None
    scheduled_at: str | None = None


class PublishReq(BaseModel):
    project_id: str = ""
    targets: list[Target] = []
    platforms: list[str] | None = None  # auto-target mode: best unpublished variant per active account
    skip_failed: bool = False


def auto_targets(db: Session, project_id: str, platforms: list[str] | None) -> list[dict]:
    """一键: for every active account on the requested platforms, the newest publishable variant of the project that
    this account hasn't received yet."""
    targets = []
    project = db.get(Project, project_id)
    # Only the all-spec series projects carry a selection; mixed projects may link a quote row too.
    quote_series = bool(project and (project.options or {}).get("quote_selection"))
    for a in db.scalars(select(Account).where(Account.status == "active")):
        if platforms and a.platform not in platforms:
            continue
        sent = set(db.scalars(select(Delivery.variant_id).where(Delivery.account_id == a.id,
                                                               Delivery.state.notin_(("failed", "canceled")))))
        if quote_series:
            variants = db.scalars(select(Variant).where(Variant.project_id == project_id,
                                                         Variant.platform == a.platform,
                                                         Variant.template_id == "quote_series",
                                                         Variant.status == "approved")
                                  .order_by(Variant.seq)).all()
            targets.extend({"variant_id": v.id, "account_id": a.id} for v in variants if v.id not in sent)
            continue
        taken = {t["variant_id"] for t in targets}
        v = db.scalar(select(Variant).where(Variant.project_id == project_id, Variant.platform == a.platform,
                                            Variant.status.in_(("passed", "approved")),
                                            Variant.id.notin_(list(sent | taken) or [""]))
                      .order_by(Variant.dedup_score, Variant.created_at.desc()))  # least similar to history first
        if v:
            targets.append({"variant_id": v.id, "account_id": a.id})
    return targets


def _targets(req: PublishReq, db: Session) -> list[dict]:
    if req.targets:
        return [t.model_dump() for t in req.targets]
    if not req.project_id:
        raise HTTPException(422, "give targets or project_id")
    t = auto_targets(db, req.project_id, req.platforms)
    if not t:
        raise HTTPException(409, "没有可发布的成片（需查重通过/人工批准，且未发往对应账号）或没有可用账号")
    return t


@router.post("/api/publish/preflight")
def preflight(req: PublishReq, db: Session = Depends(get_db)):
    targets = _targets(req, db)
    report = O.preflight(db, targets)
    return {"ok": all(r["ok"] for r in report), "targets": report}


@router.post("/api/publish/one-click")
def one_click(req: PublishReq, db: Session = Depends(get_db), who: str = Depends(require_api_key)):
    batch = O.one_click(db, _targets(req, db), who, req.project_id, req.skip_failed)
    db.refresh(batch)
    return S.batch(batch)


@router.get("/api/publish/batches")
def list_batches(limit: int = 50, db: Session = Depends(get_db)):
    return [S.batch(b) for b in db.scalars(select(PublishBatch).order_by(PublishBatch.created_at.desc()).limit(limit))]


@router.get("/api/publish/batches/{bid}")
def get_batch(bid: str, db: Session = Depends(get_db)):
    return S.batch(db.get(PublishBatch, bid) or _404())


@router.get("/api/publish/deliveries")
def list_deliveries(state: str | None = None, platform: str | None = None, limit: int = 200,
                    db: Session = Depends(get_db)):
    q = select(Delivery).order_by(Delivery.created_at.desc()).limit(min(limit, 1000))
    if state:
        q = q.where(Delivery.state.in_(state.split(",")))
    if platform:
        q = q.where(Delivery.platform == platform)
    return [S.delivery(d) for d in db.scalars(q)]


@router.get("/api/publish/deliveries/{did}")
def get_delivery(did: str, db: Session = Depends(get_db)):
    d = db.get(Delivery, did) or _404()
    out = S.delivery(d, events=True)
    v = db.get(Variant, d.variant_id)
    out["variant"] = S.variant(v)
    if d.mode == "assisted":
        out["handoff"] = handoff_info(db, d, v)
    return out


def handoff_info(db: Session, d: Delivery, v: Variant) -> dict:
    s = get_settings()
    token = signed_page_token("delivery", d.id)
    blocked_reason = ""
    ttl = 7 * 86400
    if carries_quote(v.plan):
        from ..quotes.service import validate_variant
        from datetime import datetime
        from zoneinfo import ZoneInfo

        blocked_reason = validate_variant(db, v)
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        ttl = max(1, int((midnight - now).total_seconds()))
    return {"creator_url": registry.ASSISTED_URLS.get(d.platform, ""),
            "mobile_url": "" if blocked_reason else f"{s.public_base_url.rstrip('/')}/m/deliveries/{d.id}?t={token}",
            "video_url": "" if blocked_reason else S.signed_media_url(v.file_path, ttl, absolute=True),
            "cover_url": "" if blocked_reason else S.signed_media_url(v.cover_path, ttl, absolute=True),
            "blocked_reason": blocked_reason, "title": d.title, "text": d.text,
            "hashtags": (v.copywriting or {}).get("hashtags", [])}


class ConfirmReq(BaseModel):
    post_url: str = Field(min_length=4)
    operator: str = Field(min_length=1)


@router.post("/api/publish/deliveries/{did}/confirm")
def confirm(did: str, req: ConfirmReq, db: Session = Depends(get_db)):
    d = db.get(Delivery, did) or _404()
    try:
        O.confirm_manual(db, d, req.post_url, req.operator)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return S.delivery(d)


@router.post("/api/publish/deliveries/{did}/retry")
def retry(did: str, db: Session = Depends(get_db)):
    d = db.get(Delivery, did) or _404()
    try:
        O.retry(db, d)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return S.delivery(d)


@router.post("/api/publish/deliveries/{did}/cancel")
def cancel(did: str, db: Session = Depends(get_db)):
    d = db.get(Delivery, did) or _404()
    try:
        O.cancel(db, d)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return S.delivery(d)


def _404():
    raise HTTPException(404, "not found")
