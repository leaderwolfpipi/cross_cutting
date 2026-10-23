"""API-key auth, platform token encryption and signed media URLs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from functools import lru_cache
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Header, HTTPException, Query, status

from .config import get_settings


# ------------------------------------------------------------------ API key
def require_api_key(x_api_key: str | None = Header(default=None), api_key: str | None = Query(default=None)) -> str:
    """FastAPI dependency: X-API-Key header (or ?api_key= for EventSource/downloads). Returns the caller label."""
    key = x_api_key or api_key
    if not key or not any(hmac.compare_digest(key, k) for k in get_settings().api_key_set):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid API key")
    return "key:" + hashlib.sha256(key.encode()).hexdigest()[:8]


# ------------------------------------------------------------------ token encryption
@lru_cache
def _fernet() -> Fernet:
    s = get_settings()
    if s.token_encryption_key:
        return Fernet(s.token_encryption_key.encode())
    digest = hashlib.sha256(("token-enc:" + s.secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode() if plain else ""


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:  # key rotated or data corrupted: surface as "needs re-auth" rather than crash
        raise ValueError("stored token cannot be decrypted (encryption key changed?)") from e


# ------------------------------------------------------------------ signed URLs
def _sig(path: str, exp: int) -> str:
    mac = hmac.new(get_settings().secret_key.encode(), f"{path}|{exp}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac[:18]).decode().rstrip("=")


def signed_media_url(rel_path: str, ttl_s: int = 3600, absolute: bool = False) -> str:
    """URL for a file under data_dir that browsers can load in <video>/<img> without the API key."""
    if not rel_path:
        return ""
    exp = int(time.time()) + ttl_s
    url = f"/media/{quote(rel_path)}?exp={exp}&sig={_sig(rel_path, exp)}"
    return get_settings().public_base_url.rstrip("/") + url if absolute else url


def verify_media(rel_path: str, exp: int, sig: str) -> bool:
    return exp >= time.time() and hmac.compare_digest(sig, _sig(rel_path, exp))


def signed_page_token(kind: str, ident: str, ttl_s: int = 7 * 86400) -> str:
    exp = int(time.time()) + ttl_s
    return f"{exp}.{_sig(f'{kind}:{ident}', exp)}"


def verify_page_token(kind: str, ident: str, token: str) -> bool:
    try:
        exp_s, sig = token.split(".", 1)
        exp = int(exp_s)
    except ValueError:
        return False
    return exp >= time.time() and hmac.compare_digest(sig, _sig(f"{kind}:{ident}", exp))


def random_state() -> str:
    return secrets.token_urlsafe(24)
