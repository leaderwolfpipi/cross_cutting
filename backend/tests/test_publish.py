"""Publishing state machine against the TEST-ONLY mock platforms, with injected faults.

The key property: whatever fails, each (variant, account) ends up published exactly once on the platform."""

from __future__ import annotations

import shutil
import uuid
import time

import httpx
import pytest

from app.db import SessionLocal
from app.jobs import queue
from app.models import Account, Delivery, PlatformCircuit, Project, PublishBatch, Variant
from app.publish import orchestrator as O
from app.publish.base import Tokens
from app.services.ingest import sha256_file


@pytest.fixture()
def env(settings, mock_platform, clips, monkeypatch):
    base = mock_platform["base"]
    for k, v in {"douyin_api_base": base, "kuaishou_api_base": base, "douyin_client_key": "ck",
                 "douyin_client_secret": "cs", "kuaishou_app_id": "app", "kuaishou_app_secret": "as",
                 "publish_http_timeout_s": 1.5, "publish_reconcile_grace_s": 0, "publish_max_attempts": 6,
                 "account_min_interval_s": 0, "chunk_size_mb": 1}.items():
        monkeypatch.setattr(settings, k, v)
    httpx.post(f"{base}/__reset", trust_env=False)
    with SessionLocal() as db:  # circuit state is global per platform: isolate tests from each other
        db.query(PlatformCircuit).delete()
        db.commit()
    return {"base": base, "settings": settings, "clips": clips}


def make_variant(env, platform: str, status: str = "passed", big: bool = False) -> str:
    s = env["settings"]
    with SessionLocal() as db:
        p = Project(name="t", product={}, material_ids=[], platforms=[platform])
        db.add(p)
        db.flush()
        v = Variant(project_id=p.id, platform=platform, status=status, duration=8.0,
                    copywriting={"title": f"测试 {platform} {uuid.uuid4().hex[:6]}", "description": "鲜活到港", "hashtags": ["海鲜"]},
                    plan={"clips": [], "cover": {"time": 1.0}})
        db.add(v)
        db.flush()
        vid_dir = s.path("renders", p.id)
        src = env["clips"]["base"]
        dst = vid_dir / f"{v.id}.mp4"
        if big:  # > 1 MB chunk size → fragmented upload
            with open(dst, "wb") as f:
                for _ in range(3):
                    f.write(src.read_bytes())
        else:
            shutil.copy(src, dst)
        cov = s.path("covers", p.id) / f"{v.id}.jpg"
        shutil.copy(env["clips"]["cover"], cov)
        v.file_path, v.cover_path, v.sha256 = s.rel(dst), s.rel(cov), sha256_file(dst)
        db.commit()
        return v.id


def make_account(platform: str, token: str | None = None, refresh: str | None = None, mode: str = "api") -> str:
    with SessionLocal() as db:
        a = Account(platform=platform, display_name=f"{platform}-acct", mode=mode)
        if mode == "api":
            O.store_tokens(a, Tokens(access_token=token or f"mock-{platform}-token",
                                     refresh_token=refresh or f"mock-{platform}-refresh", expires_in=86400,
                                     open_id={"douyin": "dy_open_1", "kuaishou": "ks_open_1"}[platform]))
        db.add(a)
        db.commit()
        return a.id


def faults(env, *rules):
    httpx.post(f"{env['base']}/__faults", json={"rules": list(rules)}, trust_env=False)


def drive(did: str, max_steps: int = 60) -> Delivery:
    """Run the delivery's state machine like the worker would, without waiting out long backoffs."""
    for _ in range(max_steps):
        try:
            O.step(did)
        except queue.Retry as r:
            time.sleep(min(r.delay_s, 0.4))
            continue
        with SessionLocal() as db:
            d = db.get(Delivery, did)
            if d.state in Delivery.TERMINAL or d.state in O.WAITING:
                return d
        time.sleep(0.3)
    with SessionLocal() as db:
        return db.get(Delivery, did)


def one(env, vid, aid) -> str:
    with SessionLocal() as db:
        b = O.one_click(db, [{"variant_id": vid, "account_id": aid}], "test")
        assert b.status == "running", b.preflight
        return b.deliveries[0].id


def posts(env, platform):
    return httpx.get(f"{env['base']}/__state", trust_env=False).json()["posts"][platform]


@pytest.mark.parametrize("platform", ["douyin", "kuaishou"])
def test_happy_path(env, platform):
    vid, aid = make_variant(env, platform), make_account(platform)
    d = drive(one(env, vid, aid))
    assert d.state == "published" and d.share_url and d.platform_post_id
    assert len(posts(env, platform)) == 1


def test_transient_errors_are_retried(env):
    vid, aid = make_variant(env, "douyin"), make_account("douyin")
    faults(env, {"path": "upload_video", "kind": "error500", "times": 2},
           {"path": "create_video", "kind": "rate_limit", "times": 1})
    d = drive(one(env, vid, aid))
    assert d.state == "published" and d.attempts == 3
    assert len(posts(env, "douyin")) == 1


@pytest.mark.parametrize("platform,path", [("douyin", "create_video"), ("kuaishou", "photo/publish")])
def test_lost_create_response_is_reconciled_not_duplicated(env, platform, path):
    vid, aid = make_variant(env, platform), make_account(platform)
    faults(env, {"path": path, "kind": "lost_response", "times": 1})
    did = one(env, vid, aid)
    d = drive(did)
    assert d.state == "published"
    assert len(posts(env, platform)) == 1, "the post must not be created twice"
    with SessionLocal() as db:
        msgs = [e.message for e in db.get(Delivery, did).events]
    assert any("对账找到" in m for m in msgs)


def test_create_that_never_happened_is_resubmitted_once(env):
    vid, aid = make_variant(env, "douyin"), make_account("douyin")
    faults(env, {"path": "create_video", "kind": "timeout", "times": 1})
    d = drive(one(env, vid, aid))
    assert d.state == "published" and len(posts(env, "douyin")) == 1


def test_expired_token_is_refreshed(env):
    vid, aid = make_variant(env, "kuaishou"), make_account("kuaishou")
    faults(env, {"path": "start_upload", "kind": "token_expired", "times": 1})
    d = drive(one(env, vid, aid))
    assert d.state == "published"


def test_unrefreshable_token_pauses_as_needs_auth(env):
    vid, aid = make_variant(env, "douyin"), make_account("douyin", token="revoked", refresh="unknown-refresh")
    d = drive(one(env, vid, aid))
    assert d.state == "needs_auth"
    with SessionLocal() as db:
        assert db.get(Account, aid).status == "needs_auth"
    assert posts(env, "douyin") == []


def test_fragmented_upload_resumes_after_failure(env):
    vid, aid = make_variant(env, "kuaishou", big=True), make_account("kuaishou")
    faults(env, {"path": "fragment", "kind": "error500", "times": 1})
    d = drive(one(env, vid, aid))
    assert d.state == "published"
    (post,) = posts(env, "kuaishou")
    with SessionLocal() as db:
        v = db.get(Variant, vid)
    assert post["sha256"] == v.sha256, "the platform received the exact file"


def test_preflight_blocks_unreviewed_and_duplicates(env):
    vid, aid = make_variant(env, "douyin", status="review"), make_account("douyin")
    with SessionLocal() as db:
        b = O.one_click(db, [{"variant_id": vid, "account_id": aid}], "test")
        assert b.status == "preflight_failed" and not b.deliveries
        db.get(Variant, vid).status = "approved"
        db.commit()
        b2 = O.one_click(db, [{"variant_id": vid, "account_id": aid}], "test")
        assert b2.status == "running"
        b3 = O.one_click(db, [{"variant_id": vid, "account_id": aid}], "test")
        assert b3.status == "preflight_failed"
        assert any(c["code"] == "duplicate" and not c["ok"] for c in b3.preflight[0]["checks"])


def test_assisted_channel(env):
    vid, aid = make_variant(env, "xiaohongshu"), make_account("xiaohongshu", mode="assisted")
    with SessionLocal() as db:
        b = O.one_click(db, [{"variant_id": vid, "account_id": aid}], "test")
        d = b.deliveries[0]
        assert d.state == "awaiting_operator" and d.mode == "assisted"
        O.confirm_manual(db, d, "https://www.xiaohongshu.com/explore/abc", "运营小王")
        assert d.state == "published"
        assert db.get(PublishBatch, b.id).status == "completed"
