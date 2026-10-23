"""抖音 OpenAPI adapter (scope video.create.bind).

Verified against the official docs (2026-10): upload_video (multipart field `video`, >50MB recommended / >300MB
required chunked upload, chunk 5–20MB) → create_video (JSON video_id/text/cover_tsp, returns item_id; the post is
reviewed and only visible to its author until approved). The chunked-upload, video_data, video_list and refresh
endpoint paths are configurable (DOUYIN_* settings) — confirm them in your app console before go-live.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlencode

from ..config import get_settings
from .base import (Adapter, AuthError, Http, PermanentError, PostStatus, PublishContext, RateLimitedError,
                   RecentPost, RetryableError, Tokens, chunks)

SINGLE_UPLOAD_MAX = 50 * 1024 * 1024
PART_SIZE = 20 * 1024 * 1024
AUTH_CODES = {2190002, 2190008, 28001003, 28001008, 28003017, 2190004}
RATE_CODES = {2190015, 28001014, 2100005}


def _check(body: dict, idempotent: bool = True) -> dict:
    data = body.get("data") or {}
    extra = body.get("extra") or {}
    code = int(data.get("error_code") or extra.get("error_code") or 0)
    if code == 0:
        return data
    desc = data.get("description") or extra.get("description") or extra.get("sub_description") or ""
    msg = f"抖音 error {code}: {desc} (logid {extra.get('logid', '')})"
    if code in AUTH_CODES or "token" in desc.lower():
        raise AuthError(msg)
    if code in RATE_CODES or "频率" in desc or "limit" in desc.lower():
        raise RateLimitedError(msg, retry_after=120)
    if "系统" in desc or "繁忙" in desc or "internal" in desc.lower():
        raise RetryableError(msg)
    raise PermanentError(msg, data={"code": code})


class DouyinAdapter(Adapter):
    platform = "douyin"

    def __init__(self) -> None:
        s = get_settings()
        self.base = s.douyin_api_base.rstrip("/")
        self.key, self.secret = s.douyin_client_key, s.douyin_client_secret
        self.http = Http()

    def configured(self) -> bool:
        return bool(self.key and self.secret)

    def _hdr(self, ctx: PublishContext) -> dict:
        return {"access-token": ctx.access_token}

    # ------------------------------------------------------------------ OAuth
    def authorize_url(self, state: str, redirect_uri: str) -> str:
        q = urlencode({"client_key": self.key, "response_type": "code", "scope": "user_info,video.create.bind,video.data.bind",
                       "redirect_uri": redirect_uri, "state": state})
        return f"{self.base}/platform/oauth/connect/?{q}"

    def _tokens(self, data: dict) -> Tokens:
        return Tokens(access_token=data["access_token"], refresh_token=data.get("refresh_token", ""),
                      expires_in=int(data.get("expires_in", 0)), refresh_expires_in=int(data.get("refresh_expires_in", 0)),
                      open_id=data.get("open_id", ""), scopes=data.get("scope", ""))

    def exchange_code(self, code: str, redirect_uri: str) -> Tokens:
        body = self.http.json("POST", f"{self.base}/oauth/access_token/", idempotent=True,
                              json={"client_key": self.key, "client_secret": self.secret, "code": code,
                                    "grant_type": "authorization_code"})
        return self._tokens(_check(body))

    def refresh(self, refresh_token: str, open_id: str) -> Tokens:
        body = self.http.json("POST", f"{self.base}/oauth/refresh_token/", idempotent=True,
                              data={"client_key": self.key, "grant_type": "refresh_token", "refresh_token": refresh_token})
        return self._tokens(_check(body))

    # ------------------------------------------------------------------ publish
    def upload(self, ctx: PublishContext) -> None:
        if ctx.checkpoint.get("video_id"):
            return
        size = ctx.video.stat().st_size
        q = f"open_id={ctx.open_id}"
        if size <= SINGLE_UPLOAD_MAX:
            with open(ctx.video, "rb") as f:
                body = self.http.json("POST", f"{self.base}/api/douyin/v1/video/upload_video/?{q}", idempotent=True,
                                      headers=self._hdr(ctx), files={"video": (ctx.video.name, f, "video/mp4")})
            ctx.save_checkpoint({"video_id": _check(body)["video"]["video_id"]})
            return
        cp = ctx.checkpoint
        if not cp.get("upload_id"):
            body = self.http.json("POST", f"{self.base}/api/douyin/v1/video/init_video_part_upload/?{q}",
                                  idempotent=True, headers=self._hdr(ctx))
            cp = {"upload_id": _check(body)["upload_id"], "parts_done": 0}
            ctx.save_checkpoint(cp)
        for i, buf in chunks(ctx.video, PART_SIZE, int(cp.get("parts_done", 0))):
            body = self.http.json(
                "POST", f"{self.base}/api/douyin/v1/video/upload_video_part/?{q}&upload_id={cp['upload_id']}&part_number={i + 1}",
                idempotent=True, headers=self._hdr(ctx), files={"video": (ctx.video.name, buf, "video/mp4")})
            _check(body)
            cp = {**cp, "parts_done": i + 1}
            ctx.save_checkpoint(cp)
        body = self.http.json("POST", f"{self.base}/api/douyin/v1/video/complete_video_part_upload/?{q}&upload_id={cp['upload_id']}",
                              idempotent=True, headers=self._hdr(ctx))
        ctx.save_checkpoint({**cp, "video_id": _check(body)["video"]["video_id"]})

    def create(self, ctx: PublishContext) -> str:
        body = self.http.json("POST", f"{self.base}/api/douyin/v1/video/create_video/?open_id={ctx.open_id}",
                              idempotent=False, headers={**self._hdr(ctx), "content-type": "application/json"},
                              json={"video_id": ctx.checkpoint["video_id"], "text": ctx.text[:1000],
                                    "cover_tsp": round(ctx.cover_time, 2)})
        return _check(body, idempotent=False)["item_id"]

    def status(self, ctx: PublishContext, post_id: str) -> PostStatus:
        body = self.http.json("POST", f"{self.base}/api/douyin/v1/video/video_data/?open_id={ctx.open_id}",
                              idempotent=True, headers=self._hdr(ctx), json={"item_ids": [post_id]})
        items = _check(body).get("list") or []
        if not items:
            return PostStatus("processing", detail="not visible yet")
        it = items[0]
        if it.get("is_reviewed") and int(it.get("video_status", 1)) in (1, 0):
            return PostStatus("published", share_url=it.get("share_url", ""))
        if int(it.get("video_status", 1)) in (2, 4):  # 2 = removed / 4 = review failed (per docs; verify)
            return PostStatus("rejected", detail=f"video_status {it.get('video_status')}")
        return PostStatus("processing", share_url=it.get("share_url", ""))

    def recent_posts(self, ctx: PublishContext) -> list[RecentPost]:
        body = self.http.json("GET", f"{self.base}/api/douyin/v1/video/video_list/?open_id={ctx.open_id}&cursor=0&count=20",
                              idempotent=True, headers=self._hdr(ctx))
        out = []
        for it in _check(body).get("list") or []:
            ts = it.get("create_time")
            out.append(RecentPost(it.get("item_id", ""), it.get("title", ""),
                                  datetime.utcfromtimestamp(ts) if ts else None, it.get("share_url", "")))
        return out
