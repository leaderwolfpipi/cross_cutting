"""TEST-ONLY mock of the 抖音 / 快手 open-platform publishing APIs, with fault injection.

Used by the test-suite and the end-to-end demo to prove the publishing state machine (retries, token refresh,
resumable uploads, reconciliation after a lost create response, async processing) without touching real accounts.
It mirrors the request/response shapes of the official docs; it is NOT a reimplementation of the platforms.

Run:  python -m mock_platforms.server --port 9100
Faults: POST /__faults {"rules": [{"path": "create_video", "kind": "lost_response", "times": 1}, ...]}
  kinds: error500 | rate_limit | token_expired | timeout (hang before acting) | lost_response (act, then hang)
State:  GET /__state  (uploads, posts per platform — used to assert there are no duplicate posts)
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import itertools
import time
import uuid

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI(title="mock platforms (TEST ONLY)")
HANG_S = 8.0  # longer than the publisher's HTTP timeout in tests
REVIEW_S = 3.0  # time a post stays 'processing' before it is published
STATE: dict = {}
_ids = itertools.count(1000)


def reset() -> None:
    STATE.clear()
    STATE.update({
        "faults": [], "tokens": {"mock-douyin-token": "dy_open_1", "mock-kuaishou-token": "ks_open_1"},
        "refresh": {"mock-douyin-refresh": "dy_open_1", "mock-kuaishou-refresh": "ks_open_1"},
        "uploads": {}, "parts": {}, "ks_uploads": {}, "posts": {"douyin": [], "kuaishou": []}, "calls": [],
    })


reset()


# ---------------------------------------------------------------------- fault injection
def _take_fault(path: str) -> dict | None:
    for rule in STATE["faults"]:
        if rule.get("times", 1) > 0 and rule.get("path", "") in path:
            rule["times"] = rule.get("times", 1) - 1
            return rule
    return None


def _auth_error(path: str) -> JSONResponse:
    if "douyin" in path or path.startswith("/oauth/"):
        return JSONResponse({"data": {"error_code": 2190008, "description": "access_token过期"},
                             "extra": {"error_code": 2190008, "description": "access_token过期", "logid": "mock"}})
    return JSONResponse({"result": 100200100, "error_msg": "access_token expired"})


@app.middleware("http")
async def faults(request: Request, call_next):
    path = request.url.path
    if path.startswith("/__"):
        return await call_next(request)
    STATE["calls"].append(path)
    rule = _take_fault(path)
    kind = rule["kind"] if rule else None
    if kind == "error500":
        return JSONResponse({"error": "mock internal error"}, status_code=500)
    if kind == "rate_limit":
        return JSONResponse({"error": "too many requests"}, status_code=429, headers={"Retry-After": "1"})
    if kind == "token_expired":
        return _auth_error(path)
    if kind == "timeout":
        await asyncio.sleep(HANG_S)
        return JSONResponse({"error": "gateway timeout"}, status_code=504)
    response = await call_next(request)
    if kind == "lost_response":
        await asyncio.sleep(HANG_S)  # the action happened; the client never sees the answer
    return response


@app.post("/__faults")
async def set_faults(body: dict):
    STATE["faults"] = body.get("rules", [])
    return {"ok": True}


@app.post("/__reset")
async def do_reset():
    reset()
    return {"ok": True}


@app.get("/__state")
async def state():
    return {"posts": STATE["posts"], "uploads": len(STATE["uploads"]) + len(STATE["ks_uploads"]),
            "calls": len(STATE["calls"]), "faults_left": [f for f in STATE["faults"] if f.get("times", 1) > 0]}


def _now() -> int:
    return int(time.time())


def _post_status(p: dict) -> bool:
    return time.time() - p["create_time"] >= REVIEW_S


# ---------------------------------------------------------------------- 抖音
def _dy_ok(data: dict) -> dict:
    return {"data": {"error_code": 0, "description": "", **data}, "extra": {"error_code": 0, "logid": uuid.uuid4().hex}}


def _dy_auth(request: Request, open_id: str) -> JSONResponse | None:
    tok = request.headers.get("access-token", "")
    if STATE["tokens"].get(tok) != open_id:
        return _auth_error(request.url.path)
    return None


@app.post("/oauth/access_token/")
async def dy_token(body: dict):
    tok, rt = f"dy-{uuid.uuid4().hex[:8]}", f"dyr-{uuid.uuid4().hex[:8]}"
    STATE["tokens"][tok], STATE["refresh"][rt] = "dy_open_1", "dy_open_1"
    return _dy_ok({"access_token": tok, "refresh_token": rt, "expires_in": 1296000, "refresh_expires_in": 2592000,
                   "open_id": "dy_open_1", "scope": "video.create.bind"})


@app.post("/oauth/refresh_token/")
async def dy_refresh(refresh_token: str = Form(...), client_key: str = Form(""), grant_type: str = Form("")):
    open_id = STATE["refresh"].get(refresh_token)
    if not open_id:
        return _auth_error("/oauth/")
    tok = f"dy-{uuid.uuid4().hex[:8]}"
    STATE["tokens"][tok] = open_id
    return _dy_ok({"access_token": tok, "expires_in": 1296000, "open_id": open_id})


@app.post("/api/douyin/v1/video/upload_video/")
async def dy_upload(request: Request, open_id: str, video: UploadFile = File(...)):
    if err := _dy_auth(request, open_id):
        return err
    data = await video.read()
    vid = f"@v{next(_ids)}"
    STATE["uploads"][vid] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    return _dy_ok({"video": {"video_id": vid, "width": 1080, "height": 1920}})


@app.post("/api/douyin/v1/video/init_video_part_upload/")
async def dy_init(request: Request, open_id: str):
    if err := _dy_auth(request, open_id):
        return err
    uid = f"up{next(_ids)}"
    STATE["parts"][uid] = {}
    return _dy_ok({"upload_id": uid})


@app.post("/api/douyin/v1/video/upload_video_part/")
async def dy_part(request: Request, open_id: str, upload_id: str, part_number: int, video: UploadFile = File(...)):
    if err := _dy_auth(request, open_id):
        return err
    STATE["parts"].setdefault(upload_id, {})[part_number] = await video.read()
    return _dy_ok({})


@app.post("/api/douyin/v1/video/complete_video_part_upload/")
async def dy_complete(request: Request, open_id: str, upload_id: str):
    if err := _dy_auth(request, open_id):
        return err
    parts = STATE["parts"].pop(upload_id, {})
    data = b"".join(parts[k] for k in sorted(parts))
    vid = f"@v{next(_ids)}"
    STATE["uploads"][vid] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    return _dy_ok({"video": {"video_id": vid}})


@app.post("/api/douyin/v1/video/create_video/")
async def dy_create(request: Request, open_id: str, body: dict):
    if err := _dy_auth(request, open_id):
        return err
    if body.get("video_id") not in STATE["uploads"]:
        return {"data": {"error_code": 2190016, "description": "video_id 不存在"}, "extra": {"error_code": 2190016}}
    item = f"dy_item_{next(_ids)}"
    STATE["posts"]["douyin"].append({"item_id": item, "open_id": open_id, "title": body.get("text", ""),
                                     "video_id": body["video_id"], "create_time": _now()})
    return _dy_ok({"item_id": item, "video_id": body["video_id"]})


@app.post("/api/douyin/v1/video/video_data/")
async def dy_data(request: Request, open_id: str, body: dict):
    if err := _dy_auth(request, open_id):
        return err
    out = [{"item_id": p["item_id"], "is_reviewed": _post_status(p), "video_status": 1,
            "share_url": f"https://www.douyin.com/video/{p['item_id']}"}
           for p in STATE["posts"]["douyin"] if p["item_id"] in body.get("item_ids", [])]
    return _dy_ok({"list": out})


@app.get("/api/douyin/v1/video/video_list/")
async def dy_list(request: Request, open_id: str, cursor: int = 0, count: int = 20):
    if err := _dy_auth(request, open_id):
        return err
    posts = [p for p in STATE["posts"]["douyin"] if p["open_id"] == open_id][::-1][:count]
    return _dy_ok({"list": [{"item_id": p["item_id"], "title": p["title"], "create_time": p["create_time"],
                             "is_reviewed": _post_status(p)} for p in posts], "has_more": False, "cursor": 0})


# ---------------------------------------------------------------------- 快手
def _ks_auth(access_token: str) -> JSONResponse | None:
    return None if access_token in STATE["tokens"] else _auth_error("/openapi/")


@app.get("/oauth2/access_token")
async def ks_token(code: str, app_id: str = "", app_secret: str = "", grant_type: str = ""):
    tok, rt = f"ks-{uuid.uuid4().hex[:8]}", f"ksr-{uuid.uuid4().hex[:8]}"
    STATE["tokens"][tok], STATE["refresh"][rt] = "ks_open_1", "ks_open_1"
    return {"result": 1, "access_token": tok, "refresh_token": rt, "expires_in": 172800,
            "refresh_token_expires_in": 15552000, "open_id": "ks_open_1", "scopes": ["user_video_publish"]}


@app.get("/oauth2/refresh_token")
async def ks_refresh(refresh_token: str, app_id: str = "", app_secret: str = "", grant_type: str = ""):
    open_id = STATE["refresh"].get(refresh_token)
    if not open_id:
        return {"result": 100200101, "error_msg": "invalid refresh_token"}
    tok = f"ks-{uuid.uuid4().hex[:8]}"
    STATE["tokens"][tok] = open_id
    return {"result": 1, "access_token": tok, "expires_in": 172800, "open_id": open_id}


@app.post("/openapi/photo/start_upload")
async def ks_start(request: Request, app_id: str, access_token: str):
    if err := _ks_auth(access_token):
        return err
    tok = f"ut{next(_ids)}"
    STATE["ks_uploads"][tok] = {"fragments": {}, "data": None, "owner": STATE["tokens"][access_token]}
    return {"result": 1, "upload_token": tok, "endpoint": request.url.netloc}


@app.post("/api/upload")
async def ks_upload(request: Request, upload_token: str):
    u = STATE["ks_uploads"].get(upload_token)
    if u is None:
        return {"result": 0, "error_msg": "upload_token invalid"}
    u["data"] = await request.body()
    return {"result": 1}


@app.post("/api/upload/fragment")
async def ks_fragment(request: Request, upload_token: str, fragment_id: int):
    u = STATE["ks_uploads"].get(upload_token)
    if u is None:
        return {"result": 0, "error_msg": "upload_token invalid"}
    body = await request.body()
    u["fragments"][fragment_id] = body
    return {"result": 1, "checksum": hashlib.md5(body).hexdigest(), "size": len(body)}


@app.get("/api/upload/resume")
async def ks_resume(upload_token: str):
    u = STATE["ks_uploads"].get(upload_token)
    if u is None:
        return {"result": 0, "error_msg": "upload_token invalid"}
    frags = sorted(u["fragments"])
    contiguous = next((i for i, f in enumerate(frags) if f != i), len(frags))
    return {"result": 1, "existed": bool(frags), "fragment_index": contiguous - 1, "fragment_list": frags}


@app.post("/api/upload/complete")
async def ks_complete(upload_token: str, fragment_count: int):
    u = STATE["ks_uploads"].get(upload_token)
    if u is None or sorted(u["fragments"]) != list(range(fragment_count)):
        return JSONResponse({"result": 0, "error_msg": "fragments incomplete"}, status_code=400)
    u["data"] = b"".join(u["fragments"][i] for i in range(fragment_count))
    return {"result": 1}


@app.post("/openapi/photo/publish")
async def ks_publish(app_id: str, access_token: str, upload_token: str, caption: str = Form(...),
                     cover: UploadFile = File(...)):
    if err := _ks_auth(access_token):
        return err
    u = STATE["ks_uploads"].get(upload_token)
    if u is None or u["data"] is None:
        return {"result": 100120002, "error_msg": "VIDEO_NOT_UPLOADED"}
    if u.get("published"):
        return {"result": 100100400, "error_msg": "upload_token already used"}
    u["published"] = True
    pid = f"ks_photo_{next(_ids)}"
    STATE["posts"]["kuaishou"].append({"photo_id": pid, "open_id": STATE["tokens"][access_token], "caption": caption,
                                       "create_time": _now(), "sha256": hashlib.sha256(u["data"]).hexdigest(),
                                       "cover_size": len(await cover.read())})
    return {"result": 1, "video_info": {"photo_id": pid, "caption": caption, "pending": True}}


@app.get("/openapi/photo/info")
async def ks_info(app_id: str, access_token: str, photo_id: str):
    if err := _ks_auth(access_token):
        return err
    p = next((x for x in STATE["posts"]["kuaishou"] if x["photo_id"] == photo_id), None)
    if p is None:
        return {"result": 100120001, "error_msg": "视频不存在"}
    return {"result": 1, "video_info": {"photo_id": photo_id, "pending": not _post_status(p),
                                        "play_url": f"https://www.kuaishou.com/short-video/{photo_id}"}}


@app.get("/openapi/photo/list")
async def ks_list(app_id: str, access_token: str, cursor: str = "", count: int = 20):
    if err := _ks_auth(access_token):
        return err
    owner = STATE["tokens"][access_token]
    posts = [p for p in STATE["posts"]["kuaishou"] if p["open_id"] == owner][::-1][:count]
    return {"result": 1, "video_list": [{"photo_id": p["photo_id"], "caption": p["caption"],
                                         "create_time": p["create_time"] * 1000} for p in posts]}


if __name__ == "__main__":
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--review-s", type=float, default=REVIEW_S)
    a = ap.parse_args()
    REVIEW_S = a.review_s
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")
