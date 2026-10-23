"""Worker process: `python -m app.jobs.worker --queues render --concurrency 2`.

Each slot is a thread that claims a job, runs its handler with a heartbeat keeping the lease alive, and records
the outcome. A reaper thread returns jobs of crashed workers to the queue. SIGTERM/SIGINT stop claiming and let
running jobs finish (their leases expire and are re-run if the process is killed hard).
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import threading
import time
import traceback

from ..config import get_settings
from ..db import init_db
from . import queue
from .handlers import HANDLERS

log = logging.getLogger("worker")


class Worker:
    def __init__(self, queues: tuple[str, ...], concurrency: int = 1, name: str | None = None):
        self.queues = queues
        self.concurrency = max(1, concurrency)
        self.name = name or f"{socket.gethostname()}:{os.getpid()}"
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        for i in range(self.concurrency):
            t = threading.Thread(target=self._slot, args=(f"{self.name}/{i}",), daemon=True, name=f"slot-{i}")
            t.start()
            self.threads.append(t)
        t = threading.Thread(target=self._reaper, daemon=True, name="reaper")
        t.start()
        self.threads.append(t)
        log.info("worker %s started: queues=%s concurrency=%d", self.name, ",".join(self.queues), self.concurrency)

    def _reaper(self) -> None:
        while not self.stop.wait(30):
            try:
                queue.reap_expired()
            except Exception:  # noqa: BLE001
                log.exception("reaper failed")

    def _slot(self, wid: str) -> None:
        s = get_settings()
        while not self.stop.is_set():
            try:
                ids = queue.claim(wid, self.queues, s.worker_lease_s)
            except Exception:  # noqa: BLE001 - database hiccup: back off and retry
                log.exception("claim failed")
                self.stop.wait(5)
                continue
            if not ids:
                self.stop.wait(s.worker_poll_s)
                continue
            for jid in ids:
                self._run(wid, jid)

    def _run(self, wid: str, jid: int) -> None:
        from ..db import SessionLocal
        from ..models import Job

        s = get_settings()
        with SessionLocal() as db:
            job = db.get(Job, jid)
            kind, payload = job.kind, dict(job.payload)
        handler = HANDLERS.get(kind)
        if handler is None:
            queue.fail(jid, wid, f"unknown job kind {kind}", retry_delay_s=3600)
            return
        state = {"progress": 0.0, "message": ""}
        beat_stop = threading.Event()

        def beat() -> None:
            while not beat_stop.wait(15):
                try:
                    queue.heartbeat(jid, wid, s.worker_lease_s, state["progress"], state["message"])
                except Exception:  # noqa: BLE001
                    log.exception("heartbeat failed")

        def progress(f: float, msg: str = "") -> None:
            state["progress"], state["message"] = max(0.0, min(1.0, f)), msg

        hb = threading.Thread(target=beat, daemon=True)
        hb.start()
        t0 = time.monotonic()
        try:
            result = handler(payload, progress)
            queue.finish(jid, wid, result)
            log.info("job %s %s done in %.1fs", jid, kind, time.monotonic() - t0)
        except queue.Retry as r:
            queue.fail(jid, wid, str(r), retry_delay_s=r.delay_s, count_attempt=False)
        except Exception as e:  # noqa: BLE001
            status = queue.fail(jid, wid, f"{e}\n{traceback.format_exc()[-3000:]}")
            log.warning("job %s %s failed (%s): %s", jid, kind, status, e)
        finally:
            beat_stop.set()

    def shutdown(self, timeout: float = 30) -> None:
        self.stop.set()
        for t in self.threads:
            t.join(timeout=timeout)


def start_pools(queues: tuple[str, ...], concurrency: int = 0) -> list[Worker]:
    """Render jobs get their own slots so long renders never starve publishing / analysis."""
    s = get_settings()
    pools = []
    if "render" in queues:
        pools.append(Worker(("render",), concurrency or s.render_concurrency))
    rest = tuple(q for q in queues if q != "render")
    if rest:
        pools.append(Worker(rest, concurrency or 4))
    for w in pools:
        w.start()
    return pools


def main() -> None:
    ap = argparse.ArgumentParser(description="seafood-video-system worker")
    ap.add_argument("--queues", default="analyze,render,dedup,publish")
    ap.add_argument("--concurrency", type=int, default=0, help="threads per pool (default: RENDER_CONCURRENCY / 4)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    init_db()
    pools = start_pools(tuple(q.strip() for q in args.queues.split(",") if q.strip()), args.concurrency)
    done = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    signal.signal(signal.SIGINT, lambda *_: done.set())
    done.wait()
    log.info("stopping worker…")
    for w in pools:
        w.shutdown()


if __name__ == "__main__":
    main()
