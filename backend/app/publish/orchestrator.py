"""One-click publishing: preflight → batch → per-delivery durable state machine (docs/design.md §7)."""

from __future__ import annotations

import hashlib
import logging
import random
import re
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import PLATFORM_SPECS, get_settings
from ..creative import lint
from ..db import SessionLocal, utcnow
from ..jobs import queue
from ..models import Account, Delivery, DeliveryEvent, Material, PlatformCircuit, PublishBatch, Variant
from ..security import decrypt, encrypt
from ..services.ingest import sha256_file
from . import registry
from .base import (AmbiguousError, AuthError, PermanentError, PublishContext, PublishError, RateLimitedError,
                   RetryableError, Tokens)

log = logging.getLogger(__name__)
WAITING = ("awaiting_operator", "needs_auth")



# ====================================================================== captions
def compose_text(platform: str, title: str, description: str, hashtags: list[str]) -> str:
    tags = " ".join(f"#{t.strip('#')}" for t in hashtags if t.strip("# "))
    if platform in ("douyin", "kuaishou"):
        parts = [title, description, tags]
    else:
        parts = [description, tags]
    text = "\n".join(p for p in parts if p).strip()
    return text[: PLATFORM_SPECS[platform]["text_max"]]


# ====================================================================== preflight
def _chk(code: str, ok: bool, msg: str, level: str = "error") -> dict:
    return {"code": code, "ok": ok, "level": level, "message": msg}


def account_quota(db: Session, acc: Account) -> tuple[int, int]:
    s = get_settings()
    limit = acc.daily_limit or s.account_daily_limit
    since = utcnow() - timedelta(hours=24)
    used = db.scalar(select(func.count(Delivery.id)).where(
        Delivery.account_id == acc.id, Delivery.state.notin_(("failed", "canceled")),
        Delivery.created_at >= since)) or 0
    return int(used), int(limit)


def circuit_open(db: Session, platform: str) -> datetime | None:
    c = db.get(PlatformCircuit, platform)
    return c.open_until if c and c.open_until and c.open_until > utcnow() else None


def _carries_quote(v: Variant) -> bool:
    from ..quotes.service import carries_quote

    return carries_quote(v.plan)


def preflight_target(db: Session, v: Variant, acc: Account | None, copy: dict, pending: dict[str, int]) -> list[dict]:
    s = get_settings()
    spec = PLATFORM_SPECS[v.platform]
    checks: list[dict] = []
    checks.append(_chk("variant_status", v.status in ("passed", "approved"),
                       f"成片状态 {v.status}（需查重通过或人工批准）"))
    path = s.abs(v.file_path) if v.file_path else None
    exists = bool(path and path.exists())
    checks.append(_chk("file", exists, "成片文件存在" if exists else "成片文件缺失"))
    if exists:
        ok = sha256_file(path) == v.sha256
        checks.append(_chk("integrity", ok, "文件校验一致" if ok else "文件 SHA-256 与生成时不一致（被修改？）"))
        size_mb = path.stat().st_size / 1048576
        checks.append(_chk("size", size_mb <= spec["max_size_mb"], f"{size_mb:.1f}MB / 上限 {spec['max_size_mb']}MB"))
        checks.append(_chk("duration", spec["min_duration"] <= v.duration <= spec["max_duration"],
                           f"{v.duration:.1f}s（{spec['min_duration']}–{spec['max_duration']}s）"))
    mats = {c["material_id"] for c in (v.plan or {}).get("clips", [])}
    bad = [m.filename for m in db.scalars(select(Material).where(Material.id.in_(mats or {""}))) if not m.publishable]
    checks.append(_chk("license", not bad, "素材授权可发布" if not bad else f"素材未授权/已拉黑：{', '.join(bad)}"))
    title, desc = copy.get("title", ""), copy.get("description", "")
    price_text = "\n".join([title, desc, *((t.get("text") or "") for t in (v.plan or {}).get("texts", []))])
    has_price_claim = bool(re.search(r"[¥￥]\s*\d|\d+(?:\.\d+)?\s*元|今日价|福利价|到港价|到店价", price_text))
    verified_quote = bool((v.plan or {}).get("quote"))
    checks.append(_chk("unverified_price", not has_price_claim or verified_quote,
                       "报价已关联已核实快照" if verified_quote else
                       "混剪内容未含报价" if not has_price_claim else "混剪含未核实的价格表述，请关联当天报价并重新生成"))
    from ..quotes.service import carries_quote, validate_variant

    if carries_quote(v.plan):
        quote_error = validate_variant(db, v)
        checks.append(_chk("quote_snapshot", not quote_error, quote_error or "报价日期和版本已核对"))
    if verified_quote:
        expected_copy = (v.plan or {}).get("quote", {}).get("copy")
        checks.append(_chk("quote_copy", copy == expected_copy,
                           "配文与报价快照一致" if copy == expected_copy else "报价配文已改动，请重新生成并核对"))
    hits = lint.lint(f"{title}\n{desc}")
    checks.append(_chk("ad_law", not hits, "文案合规" if not hits else
                       "广告法风险词：" + "、".join(sorted({h['match'] for h in hits}))))
    checks.append(_chk("title_len", len(title) <= spec["title_max"], f"标题 {len(title)}/{spec['title_max']} 字",
                       level="warn"))
    if acc is None:
        checks.append(_chk("account", False, "未选择账号"))
        return checks
    checks.append(_chk("account_platform", acc.platform == v.platform, f"账号平台 {acc.platform}"))
    checks.append(_chk("account_active", acc.status == "active", f"账号状态 {acc.status}"))
    if acc.mode == "api":
        if not registry.supports_api(acc.platform):
            checks.append(_chk("api_supported", False, f"{spec['name']}无开放发布接口，请将账号设为人工辅助模式"))
        else:
            ad = registry.adapter(acc.platform)
            checks.append(_chk("app_credentials", ad.configured(), "开放平台应用凭证已配置" if ad.configured()
                               else f"缺少 {acc.platform} 应用凭证（client_key/secret）"))
            has_token = bool(acc.access_token_enc)
            expiring = acc.token_expires_at and acc.token_expires_at < utcnow() + timedelta(minutes=10)
            can_refresh = bool(acc.refresh_token_enc) and (not acc.refresh_expires_at or acc.refresh_expires_at > utcnow())
            checks.append(_chk("token", has_token and (not expiring or can_refresh),
                               "令牌有效" if has_token and not expiring else
                               "令牌即将过期，将自动刷新" if has_token and can_refresh else "令牌缺失或已过期，请重新授权"))
        opened = circuit_open(db, acc.platform)
        checks.append(_chk("circuit", opened is None, "平台接口正常" if opened is None else
                           f"平台接口熔断中，{opened:%H:%M} 后恢复", level="warn"))
    used, limit = account_quota(db, acc)
    used += pending.get(acc.id, 0)
    checks.append(_chk("quota", used < limit, f"近24小时 {used}/{limit} 条"))
    pending[acc.id] = pending.get(acc.id, 0) + 1
    return checks


def resolve_copy(v: Variant, override: dict | None) -> dict:
    base = v.copywriting or {}
    o = override or {}
    return {"title": o.get("title") or base.get("title", ""), "description": o.get("description") or base.get("description", ""),
            "hashtags": o.get("hashtags") or base.get("hashtags", [])}


def preflight(db: Session, targets: list[dict]) -> list[dict]:
    """targets: [{variant_id, account_id, title?, description?, hashtags?, scheduled_at?}]"""
    out, pending = [], {}
    for t in targets:
        v = db.get(Variant, t["variant_id"])
        acc = db.get(Account, t.get("account_id") or "")
        if v is None:
            out.append({**t, "ok": False, "checks": [_chk("variant", False, "成片不存在")]})
            continue
        copy = resolve_copy(v, t)
        checks = preflight_target(db, v, acc, copy, pending)
        existing = db.scalar(select(Delivery).where(Delivery.variant_id == v.id, Delivery.account_id == (acc.id if acc else "")))
        if existing and existing.state not in ("failed", "canceled"):
            checks.append(_chk("duplicate", False, f"该成片已发往此账号（状态 {existing.state}），不会重复发布"))
        out.append({"variant_id": v.id, "account_id": acc.id if acc else None, "platform": v.platform,
                    "mode": acc.mode if acc else None, "copy": copy,
                    "ok": all(c["ok"] or c["level"] == "warn" for c in checks), "checks": checks})
    return out


# ====================================================================== one click
def one_click(db: Session, targets: list[dict], requested_by: str, project_id: str = "",
              skip_failed: bool = False) -> PublishBatch:
    report = preflight(db, targets)
    batch = PublishBatch(project_id=project_id, preflight=report, requested_by=requested_by)
    db.add(batch)
    db.flush()  # assign batch.id before deliveries reference it
    if any(not r["ok"] for r in report) and not skip_failed:
        batch.status = "preflight_failed"
        batch.summary = {"total": len(report), "blocked": sum(not r["ok"] for r in report)}
        db.commit()
        return batch
    s = get_settings()
    by_acc: dict[str, datetime] = {}
    created = 0
    for r, t in zip(report, targets):
        if not r["ok"]:
            continue
        v, acc = db.get(Variant, r["variant_id"]), db.get(Account, r["account_id"])
        old = db.scalar(select(Delivery).where(Delivery.variant_id == v.id, Delivery.account_id == acc.id))
        if old is not None:  # a failed/canceled earlier attempt: reuse the row (unique variant×account)
            d = old
            d.batch_id, d.state, d.attempts, d.last_error, d.error_class, d.checkpoint = batch.id, "queued", 0, "", "", {}
        else:
            d = Delivery(batch_id=batch.id, variant_id=v.id, account_id=acc.id, platform=v.platform)
            db.add(d)
        d.mode = acc.mode if registry.supports_api(acc.platform) else "assisted"
        d.title, d.text = r["copy"]["title"], compose_text(v.platform, **r["copy"])
        d.marker = hashlib.sha1(f"{v.id}:{acc.id}".encode()).hexdigest()[:12]
        sched = t.get("scheduled_at")
        d.scheduled_at = datetime.fromisoformat(sched).replace(tzinfo=None) if isinstance(sched, str) and sched else None
        db.flush()
        if d.mode == "assisted":
            transition(db, d, "awaiting_operator", "已生成人工发布任务（发布包 + 手机交接页）")
        else:
            # space deliveries of the same account by its minimum interval
            gap = acc.min_interval_s if acc.min_interval_s >= 0 else s.account_min_interval_s
            last = max(filter(None, [by_acc.get(acc.id), acc.last_published_at and acc.last_published_at + timedelta(seconds=gap)]),
                       default=None)
            run_at = max(filter(None, [utcnow(), d.scheduled_at, last]))
            by_acc[acc.id] = run_at + timedelta(seconds=gap)
            transition(db, d, "queued", f"排队，计划 {run_at:%m-%d %H:%M:%S} 开始")
            queue.enqueue(db, "publish", "publish_step", {"delivery_id": d.id}, dedupe_key=f"pub:{d.id}",
                          run_after=run_at, max_attempts=1000, commit=False)
        created += 1
    batch.status = "running"
    batch.summary = {"total": len(report), "created": created, "blocked": sum(not r["ok"] for r in report)}
    db.commit()
    return batch


# ====================================================================== state machine
def transition(db: Session, d: Delivery, to: str, message: str = "", data: dict | None = None) -> None:
    db.add(DeliveryEvent(delivery_id=d.id, from_state=d.state if d.id else "", to_state=to, message=message[:2000],
                         data=data or {}))
    d.state = to
    d.updated_at = utcnow()


def _record_circuit(db: Session, platform: str, ok: bool) -> None:
    s = get_settings()
    c = db.get(PlatformCircuit, platform) or PlatformCircuit(platform=platform, consecutive_failures=0)
    if ok:
        c.consecutive_failures, c.open_until = 0, None
    else:
        c.consecutive_failures += 1
        if c.consecutive_failures >= s.circuit_failures:
            c.open_until = utcnow() + timedelta(seconds=s.circuit_cooldown_s)
            c.consecutive_failures = s.circuit_failures - 1  # half-open: one failure re-opens it
    db.add(c)


def _decrypt(value: str) -> str:
    try:
        return decrypt(value)
    except ValueError as e:
        raise AuthError(str(e)) from e


def _ensure_token(db: Session, acc: Account, ad) -> str:
    if acc.token_expires_at and acc.token_expires_at < utcnow() + timedelta(minutes=10):
        _refresh(db, acc, ad)
    token = _decrypt(acc.access_token_enc)
    if not token:
        raise AuthError("账号没有访问令牌")
    return token


def _refresh(db: Session, acc: Account, ad) -> None:
    rt = _decrypt(acc.refresh_token_enc)
    if not rt:
        raise AuthError("no refresh token")
    tok = ad.refresh(rt, acc.open_id)
    store_tokens(acc, tok)
    db.commit()


def store_tokens(acc: Account, tok: Tokens) -> None:
    now = utcnow()
    acc.access_token_enc = encrypt(tok.access_token)
    if tok.refresh_token:
        acc.refresh_token_enc = encrypt(tok.refresh_token)
    acc.token_expires_at = now + timedelta(seconds=tok.expires_in) if tok.expires_in else None
    if tok.refresh_expires_in:
        acc.refresh_expires_at = now + timedelta(seconds=tok.refresh_expires_in)
    if tok.open_id:
        acc.open_id = tok.open_id
    if tok.scopes:
        acc.scopes = tok.scopes
    acc.status = "active"


def _backoff(attempts: int, err: PublishError) -> float:
    if err.retry_after:
        return err.retry_after * random.uniform(1.0, 1.25)
    return min(900.0, 4.0 * 2 ** attempts) * random.uniform(0.75, 1.25)


def step(delivery_id: str) -> None:
    """Advance one delivery as far as it can go now. Raises queue.Retry to be called again later."""
    s = get_settings()
    db = SessionLocal()
    try:
        d = db.get(Delivery, delivery_id)
        if d is None or d.state in Delivery.TERMINAL or d.state in WAITING:
            return
        v, acc = db.get(Variant, d.variant_id), db.get(Account, d.account_id)
        if v and _carries_quote(v):
            from ..quotes.service import validate_variant

            quote_error = validate_variant(db, v)
            if quote_error and d.state in ("queued", "uploading", "uploaded"):
                d.last_error, d.error_class = quote_error, "expired_quote"
                transition(db, d, "failed", f"报价核验失败：{quote_error}")
                db.commit()
                _finish_batch(db, d.batch_id)
                return
        if d.scheduled_at and d.scheduled_at > utcnow():
            raise queue.Retry((d.scheduled_at - utcnow()).total_seconds())
        opened = circuit_open(db, d.platform)
        if opened:
            raise queue.Retry((opened - utcnow()).total_seconds() + random.uniform(1, 10), "circuit open")
        ad = registry.adapter(d.platform)
        if acc.status != "active":
            transition(db, d, "needs_auth", f"账号状态 {acc.status}")
            db.commit()
            return

        def save_cp(cp: dict) -> None:
            d.checkpoint = dict(cp)
            db.commit()

        try:
            token = _ensure_token(db, acc, ad)
            ctx = PublishContext(video=s.abs(v.file_path), cover=s.abs(v.cover_path), text=d.text, title=d.title,
                                 access_token=token, open_id=acc.open_id, checkpoint=dict(d.checkpoint or {}),
                                 save_checkpoint=save_cp, cover_time=float((v.plan.get("cover") or {}).get("time", 1.0)))
            while True:
                if d.state == "queued":
                    transition(db, d, "uploading", "开始上传")
                    db.commit()
                elif d.state == "uploading":
                    ad.upload(ctx)
                    transition(db, d, "uploaded", "上传完成", {"checkpoint": d.checkpoint})
                    db.commit()
                elif d.state == "uploaded":
                    if _carries_quote(v):
                        from ..quotes.service import validate_variant

                        latest_error = validate_variant(db, v)
                        if latest_error:
                            d.last_error, d.error_class = latest_error, "expired_quote"
                            transition(db, d, "failed", f"提交前报价核验失败：{latest_error}")
                            db.commit()
                            _finish_batch(db, d.batch_id)
                            return
                    # persist intent BEFORE the non-idempotent call: a crash here is detected as 'creating' on restart
                    d.checkpoint = {**d.checkpoint, "create_started": utcnow().isoformat()}
                    transition(db, d, "creating", "提交发布")
                    db.commit()
                    ctx.checkpoint = dict(d.checkpoint)
                    post_id = ad.create(ctx)
                    d.platform_post_id, d.submitted_at = post_id, utcnow()
                    acc.last_published_at = utcnow()
                    transition(db, d, "submitted", f"平台已受理，作品 ID {post_id}")
                    _record_circuit(db, d.platform, True)
                    db.commit()
                elif d.state == "creating":
                    transition(db, d, "create_unknown", "上次提交结果未知（进程中断），先对账")
                    db.commit()
                elif d.state == "create_unknown":
                    if _reconcile(db, d, ad, ctx):
                        continue
                    if _carries_quote(v):
                        from ..quotes.service import validate_variant

                        expired = validate_variant(db, v)
                        if expired:
                            d.last_error, d.error_class = expired, "expired_quote"
                            transition(db, d, "failed", f"提交结果不明且报价已失效，请人工核对平台：{expired}")
                            db.commit()
                            _finish_batch(db, d.batch_id)
                            return
                    started = datetime.fromisoformat(d.checkpoint.get("create_started", utcnow().isoformat()))
                    if (utcnow() - started).total_seconds() < s.publish_reconcile_grace_s:
                        db.commit()
                        raise queue.Retry(20, "waiting for the post list to settle before re-submitting")
                    transition(db, d, "uploaded", "对账确认未发布成功，重新提交")
                    db.commit()
                elif d.state == "submitted":
                    st = ad.status(ctx, d.platform_post_id)
                    if st.state == "published":
                        d.published_at, d.share_url = utcnow(), st.share_url
                        transition(db, d, "published", "已发布", {"share_url": st.share_url})
                        db.commit()
                    elif st.state == "rejected":
                        transition(db, d, "platform_rejected", f"平台审核未通过：{st.detail}")
                        db.commit()
                    else:
                        waited = (utcnow() - (d.submitted_at or utcnow())).total_seconds()
                        if waited > s.publish_poll_max_s:
                            d.last_error = f"提交后 {waited / 3600:.1f} 小时仍未确认发布，请到平台后台核实"
                            db.commit()
                            return
                        db.commit()
                        raise queue.Retry(min(s.publish_poll_interval_s * (1 + waited // 600), 900), "polling")
                    _finish_batch(db, d.batch_id)
                    return
                else:
                    return
        except queue.Retry:
            raise
        except AmbiguousError as e:
            d.last_error, d.error_class = str(e), e.error_class
            transition(db, d, "create_unknown", f"提交结果不明：{e}，将对账确认")
            db.commit()
            raise queue.Retry(15, "reconcile") from e
        except AuthError as e:
            if not d.checkpoint.get("refreshed_after_auth_error"):
                try:
                    _refresh(db, acc, ad)
                    d.checkpoint = {**d.checkpoint, "refreshed_after_auth_error": True}
                    db.commit()
                    raise queue.Retry(1, "token refreshed")
                except PublishError:
                    pass
            acc.status = "needs_auth"
            d.last_error, d.error_class = str(e), e.error_class
            transition(db, d, "needs_auth", f"授权失效，请重新授权后点“重试”：{e}")
            db.commit()
        except (RetryableError, RateLimitedError) as e:
            d.attempts += 1
            d.last_error, d.error_class = str(e), e.error_class
            _record_circuit(db, d.platform, False)
            if d.attempts >= s.publish_max_attempts:
                transition(db, d, "failed", f"重试 {d.attempts} 次仍失败：{e}")
                db.commit()
                _finish_batch(db, d.batch_id)
                return
            delay = _backoff(d.attempts, e)
            db.add(DeliveryEvent(delivery_id=d.id, from_state=d.state, to_state=d.state,
                                 message=f"第 {d.attempts} 次失败（{e.error_class}），{delay:.0f}s 后重试：{e}"))
            db.commit()
            raise queue.Retry(delay, str(e)) from e
        except PermanentError as e:
            d.last_error, d.error_class = str(e), e.error_class
            transition(db, d, "failed", f"不可重试的错误：{e}")
            db.commit()
            _finish_batch(db, d.batch_id)
    finally:
        db.close()


def _reconcile(db: Session, d: Delivery, ad, ctx: PublishContext) -> bool:
    """Did a create with unknown outcome actually happen? Match our caption in the account's recent posts."""
    started = datetime.fromisoformat(d.checkpoint.get("create_started", (utcnow() - timedelta(hours=1)).isoformat()))
    head = d.text.strip()[:40]
    for p in ad.recent_posts(ctx):
        if p.caption.strip()[:40] == head and (p.created_at is None or p.created_at >= started - timedelta(minutes=5)):
            d.platform_post_id, d.submitted_at = p.post_id, utcnow()
            transition(db, d, "submitted", f"对账找到已发布作品 {p.post_id}，不重复提交")
            db.commit()
            return True
    return False


def _finish_batch(db: Session, batch_id: str) -> None:
    b = db.get(PublishBatch, batch_id)
    if b is None:
        return
    states = [d.state for d in b.deliveries]
    if any(st not in Delivery.TERMINAL for st in states):
        return
    ok = sum(st == "published" for st in states)
    b.status = "completed" if ok == len(states) else "failed" if ok == 0 else "partial"
    b.summary = {**(b.summary or {}), "published": ok, "total_deliveries": len(states)}
    db.commit()


# ====================================================================== operator actions
def confirm_manual(db: Session, d: Delivery, post_url: str, operator: str) -> None:
    if d.state != "awaiting_operator":
        raise ValueError(f"delivery is {d.state}, not awaiting an operator")
    v = db.get(Variant, d.variant_id)
    if v and _carries_quote(v):
        from ..quotes.service import validate_variant

        error = validate_variant(db, v)
        if error:
            raise ValueError(f"报价已失效：{error}")
    d.share_url, d.operator, d.published_at = post_url, operator, utcnow()
    transition(db, d, "published", f"{operator} 确认已在平台发布", {"post_url": post_url})
    db.commit()
    _finish_batch(db, d.batch_id)


def retry(db: Session, d: Delivery) -> None:
    if d.state not in ("failed", "needs_auth", "platform_rejected", "submitted"):
        raise ValueError(f"cannot retry a delivery in state {d.state}")
    v = db.get(Variant, d.variant_id)
    if v and _carries_quote(v):
        from ..quotes.service import validate_variant

        error = validate_variant(db, v)
        if error:
            raise ValueError(f"报价已失效：{error}")
    if d.state == "needs_auth":
        acc = db.get(Account, d.account_id)
        if acc.status != "active":
            raise ValueError("请先重新授权该账号")
    d.attempts, d.last_error, d.error_class = 0, "", ""
    to = "submitted" if d.platform_post_id else ("uploaded" if d.checkpoint.get("video_id") or d.checkpoint.get("uploaded") else "queued")
    transition(db, d, to, "人工重试")
    queue.enqueue(db, "publish", "publish_step", {"delivery_id": d.id}, dedupe_key=f"pub:{d.id}", max_attempts=1000,
                  commit=False)
    db.commit()


def cancel(db: Session, d: Delivery) -> None:
    if d.state in ("creating", "create_unknown", "submitted", "published"):
        raise ValueError("已提交到平台的任务不能取消，请到平台后台处理")
    transition(db, d, "canceled", "已取消")
    db.commit()
    _finish_batch(db, d.batch_id)


def video_path(v: Variant) -> Path:
    return get_settings().abs(v.file_path)
