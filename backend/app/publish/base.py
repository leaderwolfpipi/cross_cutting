"""Publishing adapter contract and error taxonomy.

The orchestrator never guesses: every adapter failure is classified as
  RetryableError  — nothing happened on the platform side (or the call is idempotent): retry with backoff;
  AmbiguousError  — a non-idempotent call may have taken effect (timeout after sending a create): reconcile first;
  AuthError       — token invalid/expired: refresh, else pause the delivery as needs_auth;
  PermanentError  — the request itself is wrong (validation, content refused): stop, tell the operator.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import httpx

from ..config import get_settings


class PublishError(Exception):
    error_class = "error"

    def __init__(self, message: str, *, retry_after: float | None = None, data: dict | None = None):
        super().__init__(message)
        self.retry_after = retry_after
        self.data = data or {}


class RetryableError(PublishError):
    error_class = "retryable"


class RateLimitedError(RetryableError):
    error_class = "rate_limited"


class AmbiguousError(PublishError):
    error_class = "ambiguous"


class AuthError(PublishError):
    error_class = "auth"


class PermanentError(PublishError):
    error_class = "permanent"


@dataclass
class Tokens:
    access_token: str
    refresh_token: str = ""
    expires_in: int = 0
    refresh_expires_in: int = 0
    open_id: str = ""
    scopes: str = ""


@dataclass
class PostStatus:
    state: str  # processing | published | rejected
    share_url: str = ""
    detail: str = ""


@dataclass
class RecentPost:
    post_id: str
    caption: str
    created_at: datetime | None = None
    share_url: str = ""


@dataclass
class PublishContext:
    video: Path
    cover: Path
    text: str  # full caption (title + body + #tags)
    title: str
    access_token: str
    open_id: str
    checkpoint: dict[str, Any]
    save_checkpoint: Callable[[dict[str, Any]], None]  # persists after every resumable step
    cover_time: float = 1.0
    hashtags: list[str] = field(default_factory=list)


class Adapter:
    platform: str = ""
    mode: str = "api"

    def configured(self) -> bool:
        return True

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        raise PermanentError(f"{self.platform} does not support OAuth in this system")

    def exchange_code(self, code: str, redirect_uri: str) -> Tokens:
        raise PermanentError("not supported")

    def refresh(self, refresh_token: str, open_id: str) -> Tokens:
        raise AuthError("token refresh not supported")

    def upload(self, ctx: PublishContext) -> None:
        raise NotImplementedError

    def create(self, ctx: PublishContext) -> str:
        raise NotImplementedError

    def status(self, ctx: PublishContext, post_id: str) -> PostStatus:
        raise NotImplementedError

    def recent_posts(self, ctx: PublishContext) -> list[RecentPost]:
        return []


class Http:
    """httpx wrapper that turns transport problems into the taxonomy above."""

    LOOPBACK = ("127.0.0.1", "localhost", "::1")

    def __init__(self, timeout: float | None = None):
        t = timeout or get_settings().publish_http_timeout_s
        # platform hosts honour HTTP(S)_PROXY (corporate egress); loopback (local gateways, the test mock) never does
        self.client = httpx.Client(timeout=t, follow_redirects=False)
        self.local = httpx.Client(timeout=t, follow_redirects=False, trust_env=False)

    def request(self, method: str, url: str, *, idempotent: bool, **kw) -> httpx.Response:
        client = self.local if httpx.URL(url).host in self.LOOPBACK else self.client
        try:
            r = client.request(method, url, **kw)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise RetryableError(f"connect failed: {e}") from e  # nothing was sent
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError, httpx.ReadError) as e:
            if idempotent:
                raise RetryableError(f"network error: {e}") from e
            raise AmbiguousError(f"no response to {method} {url.split('?')[0]}: {e}") from e
        except httpx.HTTPError as e:
            raise RetryableError(f"http error: {e}") from e
        if r.status_code == 429:
            ra = r.headers.get("retry-after")
            raise RateLimitedError("rate limited (429)", retry_after=float(ra) if ra and ra.isdigit() else 60.0)
        if r.status_code in (401, 403):
            raise AuthError(f"HTTP {r.status_code}: {r.text[:200]}")
        if r.status_code >= 500:
            if not idempotent and r.status_code in (502, 504):
                raise AmbiguousError(f"gateway error {r.status_code} on create")
            raise RetryableError(f"server error {r.status_code}: {r.text[:200]}")
        if r.status_code >= 400:
            raise PermanentError(f"HTTP {r.status_code}: {r.text[:300]}")
        return r

    def json(self, method: str, url: str, *, idempotent: bool, **kw) -> dict:
        r = self.request(method, url, idempotent=idempotent, **kw)
        try:
            return r.json()
        except ValueError as e:
            if idempotent:
                raise RetryableError(f"invalid JSON from platform: {r.text[:200]}") from e
            raise AmbiguousError(f"invalid JSON after create: {r.text[:200]}") from e

    def close(self) -> None:
        self.client.close()
        self.local.close()


def chunks(path: Path, size: int, start_index: int = 0):
    with open(path, "rb") as f:
        f.seek(start_index * size)
        i = start_index
        while True:
            buf = f.read(size)
            if not buf:
                break
            yield i, buf
            i += 1


def now_ts() -> int:
    return int(time.time())
