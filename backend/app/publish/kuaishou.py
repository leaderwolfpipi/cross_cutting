"""快手 OpenAPI adapter (scope user_video_publish).

Verified against the official 创建视频 doc (2026-10): start_upload → {upload_token, endpoint}; upload to
http://{endpoint}/api/upload (binary, <10MB) or /api/upload/fragment + /api/upload/resume + /api/upload/complete;
publish (multipart cover + caption) is ASYNCHRONOUS — success does not mean visible, query photo status until
`pending` is false. The photo info / list query paths are configurable; confirm them in the console.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlencode

from ..config import get_settings
from .base import (Adapter, AuthError, Http, PermanentError, PostStatus, PublishContext, RateLimitedError,
                   RecentPost, RetryableError, Tokens, chunks)

DIRECT_MAX = 10 * 1024 * 1024


def _check(body: dict) -> dict:
    try:
        code = int(body.get("result", 0))
    except (TypeError, ValueError):
        code = 0
    if code == 1:
        return body
    msg = f"快手 error {body.get('result')}: {body.get('error_msg') or body.get('error') or body}"
    low = msg.lower()
    if "access_token" in low or ("token" in low and "upload" not in low):
        raise AuthError(msg)
    if code == 100100402 or "limit" in low:
        raise RateLimitedError(msg, retry_after=600)
    if "server_error" in low or "system" in low:
        raise RetryableError(msg)
    raise PermanentError(msg, data={"code": code})


class KuaishouAdapter(Adapter):
    platform = "kuaishou"

    def __init__(self) -> None:
        s = get_settings()
        self.base = s.kuaishou_api_base.rstrip("/")
        self.app_id, self.secret = s.kuaishou_app_id, s.kuaishou_app_secret
        self.scheme = s.kuaishou_upload_scheme
        self.chunk = min(10, max(1, s.chunk_size_mb)) * 1024 * 1024
        self.http = Http()

    def configured(self) -> bool:
        return bool(self.app_id and self.secret)

    def _q(self, ctx: PublishContext, **extra) -> str:
        return urlencode({"app_id": self.app_id, "access_token": ctx.access_token, **extra})

    # ------------------------------------------------------------------ OAuth
    def authorize_url(self, state: str, redirect_uri: str) -> str:
        q = urlencode({"app_id": self.app_id, "scope": "user_info,user_video_publish,user_video_info",
                       "response_type": "code", "ui": "pc", "redirect_uri": redirect_uri, "state": state})
        return f"{self.base}/oauth2/authorize?{q}"

    def _tokens(self, b: dict) -> Tokens:
        return Tokens(access_token=b["access_token"], refresh_token=b.get("refresh_token", ""),
                      expires_in=int(b.get("expires_in", 0)), refresh_expires_in=int(b.get("refresh_token_expires_in", 0)),
                      open_id=b.get("open_id", ""), scopes=",".join(b.get("scopes") or []))

    def exchange_code(self, code: str, redirect_uri: str) -> Tokens:
        b = self.http.json("GET", f"{self.base}/oauth2/access_token?" + urlencode(
            {"app_id": self.app_id, "app_secret": self.secret, "code": code, "grant_type": "authorization_code"}),
            idempotent=True)
        return self._tokens(_check(b))

    def refresh(self, refresh_token: str, open_id: str) -> Tokens:
        b = self.http.json("GET", f"{self.base}/oauth2/refresh_token?" + urlencode(
            {"app_id": self.app_id, "app_secret": self.secret, "refresh_token": refresh_token,
             "grant_type": "refresh_token"}), idempotent=True)
        return self._tokens(_check(b))

    # ------------------------------------------------------------------ publish
    def _gateway(self, cp: dict) -> str:
        ep = cp["endpoint"]
        return ep if ep.startswith("http") else f"{self.scheme}://{ep}"

    def upload(self, ctx: PublishContext) -> None:
        cp = dict(ctx.checkpoint)
        if cp.get("uploaded"):
            return
        if not cp.get("upload_token"):
            b = _check(self.http.json("POST", f"{self.base}/openapi/photo/start_upload?{self._q(ctx)}", idempotent=True))
            cp = {"upload_token": b["upload_token"], "endpoint": b["endpoint"], "fragments_done": 0}
            ctx.save_checkpoint(cp)
        gw, tok = self._gateway(cp), cp["upload_token"]
        size = ctx.video.stat().st_size
        try:
            if size < DIRECT_MAX:
                _check(self.http.json("POST", f"{gw}/api/upload?upload_token={tok}", idempotent=True,
                                      content=ctx.video.read_bytes(), headers={"Content-Type": "video/mp4"}))
                ctx.save_checkpoint({**cp, "uploaded": True})
                return
            if cp.get("fragments_done"):  # after a restart, ask the gateway what it already has
                r = self.http.json("GET", f"{gw}/api/upload/resume?upload_token={tok}", idempotent=True)
                if int(r.get("result", 0)) == 1 and r.get("existed"):
                    cp["fragments_done"] = int(r.get("fragment_index", -1)) + 1
            count = (size + self.chunk - 1) // self.chunk
            for i, buf in chunks(ctx.video, self.chunk, int(cp.get("fragments_done", 0))):
                _check(self.http.json("POST", f"{gw}/api/upload/fragment?upload_token={tok}&fragment_id={i}",
                                      idempotent=True, content=buf, headers={"Content-Type": "application/octet-stream"}))
                cp["fragments_done"] = i + 1
                ctx.save_checkpoint(cp)
            _check(self.http.json("POST", f"{gw}/api/upload/complete?upload_token={tok}&fragment_count={count}",
                                  idempotent=True))
            ctx.save_checkpoint({**cp, "uploaded": True})
        except PermanentError as e:
            # an expired / unknown upload_token: start over with a fresh one next attempt
            ctx.save_checkpoint({})
            raise RetryableError(f"upload restarted: {e}") from e

    def create(self, ctx: PublishContext) -> str:
        cp = ctx.checkpoint
        with open(ctx.cover, "rb") as cover:
            b = self.http.json("POST", f"{self.base}/openapi/photo/publish?{self._q(ctx, upload_token=cp['upload_token'])}",
                               idempotent=False, files={"cover": (ctx.cover.name, cover, "image/jpeg")},
                               data={"caption": ctx.text[:500]})
        info = _check(b).get("video_info") or {}
        if not info.get("photo_id"):
            raise PermanentError(f"publish returned no photo_id: {b}")
        return str(info["photo_id"])

    def status(self, ctx: PublishContext, post_id: str) -> PostStatus:
        b = _check(self.http.json("GET", f"{self.base}/openapi/photo/info?{self._q(ctx, photo_id=post_id)}",
                                  idempotent=True))
        info = b.get("video_info") or {}
        if info.get("pending"):
            return PostStatus("processing")
        if info.get("deleted") or info.get("status") in ("REJECTED", "BLOCKED"):
            return PostStatus("rejected", detail=str(info.get("status", "")))
        return PostStatus("published", share_url=info.get("play_url", ""))

    def recent_posts(self, ctx: PublishContext) -> list[RecentPost]:
        b = _check(self.http.json("GET", f"{self.base}/openapi/photo/list?{self._q(ctx, cursor='', count=20)}",
                                  idempotent=True))
        out = []
        for it in b.get("video_list") or []:
            ts = it.get("create_time")
            out.append(RecentPost(str(it.get("photo_id", "")), it.get("caption", ""),
                                  datetime.utcfromtimestamp(ts / 1000) if ts else None, it.get("play_url", "")))
        return out
