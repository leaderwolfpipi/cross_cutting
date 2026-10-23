"""Health, dashboard, job monitor, signed media and the mobile hand-off page."""

from __future__ import annotations

import html
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..config import PLATFORM_SPECS, get_settings
from ..creative.templates import TEMPLATES
from ..db import get_db, utcnow
from ..jobs import queue
from ..media import ffmpeg as F
from ..models import Account, Delivery, Job, Material, Project, Variant
from ..render.ffmpeg_backend import FFmpegBackend
from ..render.jianying import JianyingBackend
from ..security import verify_media, verify_page_token
from . import serializers as S

router = APIRouter(tags=["系统"])
public = APIRouter(tags=["公开"])


@public.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/api/system/info")
def info():
    s = get_settings()
    jy = JianyingBackend()
    return {
        "version": "2.0.0", "env": s.env,
        "render_backends": {"jianying": jy.available(), "ffmpeg": FFmpegBackend().available(), "selected": s.render_backend},
        "hdr_tonemap": "tonemap_opencl" if F.opencl_tonemap_ok() else "colorspace",
        "copywriter": s.copywriter, "dedup": {"scope": s.dedup_scope, "hamming": s.dedup_hamming},
        "platforms": {k: {"name": v["name"], "mode": v["mode"], "canvas": f"{v['width']}x{v['height']}"}
                      for k, v in PLATFORM_SPECS.items()},
        "templates": {t.id: t.name for t in TEMPLATES.values()},
    }


@router.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)):
    since = utcnow() - timedelta(days=7)
    count = lambda model, *w: db.scalar(select(func.count()).select_from(model).where(*w)) or 0  # noqa: E731
    by_state = dict(db.execute(select(Delivery.state, func.count()).group_by(Delivery.state)).all())
    by_variant = dict(db.execute(select(Variant.status, func.count()).group_by(Variant.status)).all())
    by_decision = dict(db.execute(select(Variant.dedup_decision, func.count()).where(Variant.dedup_decision != "")
                                  .group_by(Variant.dedup_decision)).all())
    return {
        "materials": count(Material), "materials_ready": count(Material, Material.status == "ready"),
        "projects": count(Project), "variants": count(Variant), "variants_7d": count(Variant, Variant.created_at >= since),
        "variant_status": by_variant, "dedup_decisions": by_decision, "delivery_states": by_state,
        "published_7d": count(Delivery, Delivery.state == "published", Delivery.published_at >= since),
        "awaiting_operator": by_state.get("awaiting_operator", 0), "needs_auth": by_state.get("needs_auth", 0),
        "accounts": count(Account), "accounts_need_auth": count(Account, Account.status == "needs_auth"),
        "jobs": dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all()),
    }


@router.get("/api/jobs")
def jobs(status: str | None = None, queue_name: str | None = None, limit: int = 100, db: Session = Depends(get_db)):
    q = select(Job).order_by(Job.id.desc()).limit(min(limit, 500))
    if status:
        q = q.where(Job.status.in_(status.split(",")))
    if queue_name:
        q = q.where(Job.queue == queue_name)
    return [S.job(j) for j in db.scalars(q)]


@router.post("/api/jobs/{jid}/retry")
def retry_job(jid: int, db: Session = Depends(get_db)):
    j = db.get(Job, jid)
    if j is None or j.status not in ("dead", "failed"):
        raise HTTPException(409, "only dead/failed jobs can be retried")
    queue.enqueue(db, j.queue, j.kind, j.payload, dedupe_key=j.dedupe_key, max_attempts=j.max_attempts)
    return {"requeued": True}


# ---------------------------------------------------------------------- signed media (no API key; HMAC in URL)
@public.get("/media/{rel:path}")
def media(rel: str, exp: int, sig: str):
    if not verify_media(rel, exp, sig):
        raise HTTPException(403, "link expired or invalid")
    try:
        path = get_settings().abs(rel)
    except ValueError as e:
        raise HTTPException(403, str(e)) from e
    if not path.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(path)  # supports Range requests (video seeking)


@public.get("/m/deliveries/{did}", response_class=HTMLResponse)
def mobile_handoff(did: str, t: str, db: Session = Depends(get_db)):
    """Phone page for 视频号 / 小红书: save the video, copy the caption, then confirm with the post link."""
    if not verify_page_token("delivery", did, t):
        raise HTTPException(403, "link expired or invalid")
    d = db.get(Delivery, did)
    if d is None:
        raise HTTPException(404, "not found")
    v = db.get(Variant, d.variant_id)
    e = html.escape
    video = S.signed_media_url(v.file_path, 7 * 86400)
    cover = S.signed_media_url(v.cover_path, 7 * 86400)
    tags = " ".join(f"#{x}" for x in (v.copywriting or {}).get("hashtags", []))
    name = PLATFORM_SPECS[d.platform]["name"]
    done = d.state == "published"
    return HTMLResponse(f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(name)}发布 · {e(d.title)}</title>
<style>body{{font-family:-apple-system,system-ui,sans-serif;margin:0;padding:16px;background:#f6f7f9;color:#1d2129}}
.card{{background:#fff;border-radius:12px;padding:14px;margin-bottom:12px}}video{{width:100%;border-radius:8px;background:#000}}
textarea{{width:100%;box-sizing:border-box;min-height:120px;font-size:15px;border:1px solid #ddd;border-radius:8px;padding:8px}}
a.btn,button{{display:block;width:100%;text-align:center;padding:12px;margin-top:8px;border-radius:8px;border:0;
background:#e8f3ff;color:#165dff;font-size:16px;text-decoration:none}}.primary{{background:#165dff!important;color:#fff!important}}
input{{width:100%;box-sizing:border-box;padding:10px;font-size:15px;border:1px solid #ddd;border-radius:8px;margin-top:6px}}
.ok{{color:#00b42a}}</style></head><body>
<h3>{e(name)} 发布任务</h3>
<div class="card"><video src="{video}" controls playsinline poster="{cover}"></video>
<a class="btn" href="{video}" download>① 保存视频</a><a class="btn" href="{cover}" download>保存封面</a></div>
<div class="card"><b>标题</b><textarea id="title" style="min-height:40px">{e(d.title)}</textarea>
<button onclick="cp('title')">复制标题</button><b>正文与话题</b><textarea id="body">{e(d.text)}</textarea>
<button onclick="cp('body')">② 复制正文</button><p style="color:#86909c">话题：{e(tags)}</p></div>
<div class="card">③ 在{e(name)} App 中发布后，粘贴作品链接确认：
{'<p class="ok">已确认发布 ✓ ' + e(d.share_url) + '</p>' if done else
 '<input id="url" placeholder="作品链接"><input id="op" placeholder="你的名字"><button class="primary" onclick="confirmPost()">我已发布</button>'}
<p id="msg"></p></div>
<script>
function cp(id){{const t=document.getElementById(id);t.select();navigator.clipboard?navigator.clipboard.writeText(t.value):document.execCommand('copy');}}
async function confirmPost(){{const r=await fetch('/m/deliveries/{did}/confirm?t={e(t)}',{{method:'POST',headers:{{'Content-Type':'application/json'}},
body:JSON.stringify({{post_url:document.getElementById('url').value,operator:document.getElementById('op').value}})}});
document.getElementById('msg').textContent=r.ok?'已确认 ✓':'失败：'+(await r.text());if(r.ok)setTimeout(()=>location.reload(),800);}}
</script></body></html>""")


@public.post("/m/deliveries/{did}/confirm")
def mobile_confirm(did: str, t: str, body: dict, db: Session = Depends(get_db)):
    from ..publish import orchestrator as O

    if not verify_page_token("delivery", did, t):
        raise HTTPException(403, "link expired or invalid")
    d = db.get(Delivery, did)
    url, op = str(body.get("post_url", "")).strip(), str(body.get("operator", "")).strip()
    if d is None or len(url) < 4 or not op:
        raise HTTPException(422, "请填写作品链接和姓名")
    try:
        O.confirm_manual(db, d, url, f"mobile:{op}")
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}
