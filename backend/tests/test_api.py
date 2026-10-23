"""HTTP API smoke tests (auth, validation, signed media, upload safety)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

H = {"X-API-Key": "test-key"}


@pytest.fixture(scope="module")
def client(isolated_settings):
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def test_health_is_public_and_api_requires_key(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/projects", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/projects", headers=H).status_code == 200


def test_system_info_reports_backends(client):
    info = client.get("/api/system/info", headers=H).json()
    assert info["render_backends"]["ffmpeg"] is True and "douyin" in info["platforms"]


def test_project_validation(client):
    r = client.post("/api/projects", headers=H, json={"name": "x", "platforms": ["tiktok"]})
    assert r.status_code == 422
    r = client.post("/api/projects", headers=H, json={"name": "春季到港", "platforms": ["douyin", "xiaohongshu"],
                                                      "product": {"items": ["东星斑"]}})
    assert r.status_code == 200
    pid = r.json()["id"]
    assert client.post(f"/api/projects/{pid}/generate", headers=H, json={"count": 1}).status_code == 409  # no materials


def test_upload_rejects_unknown_types_and_never_uses_client_path(client, clips, settings):
    r = client.post("/api/materials/upload", headers=H, files={"file": ("../../evil.sh", b"#!/bin/sh", "text/plain")})
    assert r.status_code == 415
    data = clips["base"].read_bytes()
    r = client.post("/api/materials/upload", headers=H, data={"product": "测试", "license_type": "self_shot"},
                    files={"file": ("../../x/../clip.mp4", data, "video/mp4")})
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["filename"] == "clip.mp4" and m["status"] == "uploaded"
    stored = list((settings.data_dir / "materials").glob("*.mp4"))
    assert stored and all(".." not in str(p) for p in stored)
    dup = client.post("/api/materials/upload", headers=H, files={"file": ("again.mp4", data, "video/mp4")})
    assert dup.status_code == 409


def test_signed_media_urls(client, settings):
    from app.security import signed_media_url

    (settings.data_dir / "covers" / "x.txt").write_text("hi")
    url = signed_media_url("covers/x.txt")
    assert client.get(url).text == "hi"
    assert client.get(url.replace("sig=", "sig=x")).status_code == 403
    # an encoded traversal reaches the media route itself, which must refuse it
    assert client.get("/media/..%2Fdb%2Fseafood.db?exp=9999999999&sig=abc").status_code in (403, 404)
    r = client.get("/media/../db/seafood.db?exp=9999999999&sig=abc")  # client-normalised: never the database file
    assert b"SQLite format" not in r.content


def test_assisted_only_platform_rejects_api_mode(client):
    r = client.post("/api/accounts", headers=H, json={"platform": "xiaohongshu", "mode": "api"})
    assert r.status_code == 422
    r = client.post("/api/accounts", headers=H, json={"platform": "xiaohongshu", "mode": "assisted",
                                                      "display_name": "门店小红书"})
    assert r.status_code == 200 and "access_token" not in r.text
