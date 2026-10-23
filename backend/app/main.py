"""FastAPI application: `uvicorn app.main:app`."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import materials, projects, publish, system
from .config import BACKEND_DIR, get_settings
from .db import init_db
from .security import require_api_key

log = logging.getLogger("api")
FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    init_db()
    if s.env == "prod" and ("change-me" in s.secret_key or "change-me" in s.api_keys):
        raise RuntimeError("set SECRET_KEY and API_KEYS before running with ENV=prod")
    pools = []
    if s.embedded_worker:
        from .jobs.worker import start_pools

        pools = start_pools(tuple(q.strip() for q in s.embedded_worker_queues.split(",") if q.strip()))
    yield
    for w in pools:
        w.shutdown(timeout=5)


def create_app() -> FastAPI:
    s = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = FastAPI(title="海鲜视频创意混剪 · 查重 · 一键发布", version="2.0.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in s.cors_origins.split(",") if o.strip()],
                       allow_methods=["*"], allow_headers=["*"], allow_credentials=False)

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        t0 = time.monotonic()
        try:
            resp = await call_next(request)
        except Exception:  # noqa: BLE001 - log with the request id, return a clean 500
            log.exception("unhandled error rid=%s %s %s", rid, request.method, request.url.path)
            resp = JSONResponse({"detail": "internal error", "request_id": rid}, status_code=500)
        resp.headers["x-request-id"] = rid
        if request.url.path.startswith("/api/"):
            log.info("%s %s %s %.0fms rid=%s", request.method, request.url.path, resp.status_code,
                     (time.monotonic() - t0) * 1000, rid)
        return resp

    auth = [Depends(require_api_key)]
    for r in (materials.router, projects.router, publish.router, system.router):
        app.include_router(r, dependencies=auth)
    app.include_router(system.public)
    app.include_router(publish.public)

    if FRONTEND_DIST.is_dir():  # production: serve the built SPA from the API process
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith(("api/", "media/", "m/", "oauth/")):
                return JSONResponse({"detail": "not found"}, status_code=404)
            f = (FRONTEND_DIST / path).resolve()
            if path and f.is_file() and f.is_relative_to(FRONTEND_DIST.resolve()):
                return FileResponse(f)
            return FileResponse(FRONTEND_DIST / "index.html")
    return app


app = create_app()
