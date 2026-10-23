"""剪映 me render backend: drives the jianying-me MCP server (stdio JSON-RPC) to build a project from the EDL and
export it. One server process per render job: crashes stay isolated and the .jmeproj is left behind so an editor
can open it in the 剪映 me app for manual fine-tuning."""

from __future__ import annotations

import json
import logging
import os
import queue
import shlex
import subprocess
import threading
import time
from pathlib import Path

from ..config import get_settings
from ..media import ffmpeg as F
from .base import Progress, RenderError, RenderResult

log = logging.getLogger(__name__)

DEFAULT_COMMANDS = (
    "/Applications/JianyingMe.app/Contents/MacOS/JianyingMe --mcp",
    str(Path.home() / "Applications/JianyingMe.app/Contents/MacOS/JianyingMe") + " --mcp",
)


class McpClient:
    """Minimal MCP stdio client (newline-delimited JSON-RPC 2.0) with per-call timeouts and progress callbacks."""

    def __init__(self, argv: list[str], timeout: float, env: dict | None = None):
        self.timeout = timeout
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8", bufsize=1, env={**os.environ, **(env or {})})
        self.inbox: queue.Queue = queue.Queue()
        self.stderr_tail: list[str] = []
        self._next = 0
        self.on_progress: Progress | None = None
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self):
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self.inbox.put(json.loads(line))
            except ValueError:
                log.debug("non-JSON from MCP server: %s", line[:200])
        self.inbox.put(None)  # EOF

    def _read_stderr(self):
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self.stderr_tail = (self.stderr_tail + [line.rstrip()])[-20:]

    def _send(self, msg: dict) -> None:
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise RenderError(f"剪映 me MCP server exited: {' | '.join(self.stderr_tail[-5:])}") from e

    def request(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        self._next += 1
        rid = self._next
        msg = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
        if method == "tools/call":
            msg["params"]["_meta"] = {"progressToken": rid}
        self._send(msg)
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise RenderError(f"剪映 me MCP {method} timed out")
            try:
                m = self.inbox.get(timeout=min(left, 5))
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise RenderError(f"剪映 me MCP server died: {' | '.join(self.stderr_tail[-5:])}") from None
                continue
            if m is None:
                raise RenderError(f"剪映 me MCP server closed: {' | '.join(self.stderr_tail[-5:])}")
            if m.get("id") == rid:
                if "error" in m:
                    raise RenderError(f"{method}: {m['error'].get('message')}")
                return m["result"]
            if m.get("method") == "notifications/progress" and self.on_progress:
                p = m.get("params", {})
                total = p.get("total") or 1
                self.on_progress(float(p.get("progress", 0)) / float(total), p.get("message", ""))

    def initialize(self) -> dict:
        r = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "seafood-video-system", "version": "2.0"}}, timeout=60)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return r

    def call(self, tool: str, timeout: float | None = None, **arguments) -> dict:
        r = self.request("tools/call", {"name": tool, "arguments": arguments}, timeout=timeout)
        content = r.get("content") or []
        if r.get("isError"):
            text = next((c.get("text") for c in content if c.get("type") == "text"), "unknown error")
            raise RenderError(f"{tool}: {text}")
        if r.get("structuredContent") is not None:
            return r["structuredContent"]
        text = next((c.get("text") for c in content if c.get("type") == "text"), "{}")
        try:
            return json.loads(text)
        except ValueError:
            return {"text": text}

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except (subprocess.TimeoutExpired, OSError):
            self.proc.kill()
            self.proc.wait()


class JianyingBackend:
    name = "jianying"

    def __init__(self) -> None:
        s = get_settings()
        self.argv = shlex.split(s.jianying_mcp_cmd) if s.jianying_mcp_cmd else self._discover()
        self.timeout = s.jianying_timeout_s

    @staticmethod
    def _discover() -> list[str]:
        for cmd in DEFAULT_COMMANDS:
            argv = shlex.split(cmd)
            if Path(argv[0]).exists():
                return argv
        return []

    def available(self) -> bool:
        return bool(self.argv) and Path(self.argv[0]).exists()

    def render(self, plan: dict, out_video: Path, out_cover: Path, work_dir: Path,
               progress: Progress | None = None) -> RenderResult:
        if not self.available():
            raise RenderError("剪映 me is not installed (set JIANYING_MCP_CMD)")
        t0 = time.monotonic()
        work_dir.mkdir(parents=True, exist_ok=True)
        proj_path = work_dir / f"{out_video.stem}.jmeproj"
        mcp = McpClient(self.argv + ["--projects-dir", str(work_dir)], self.timeout)
        warnings: list[str] = []
        try:
            mcp.initialize()
            build_timeline(mcp, plan, proj_path, warnings, progress)
            if progress:
                progress(0.3, "exporting")
            mcp.on_progress = (lambda f, msg: progress(0.3 + 0.65 * f, msg)) if progress else None
            out_video.parent.mkdir(parents=True, exist_ok=True)
            res = mcp.call("export_video", path=str(out_video), preset="project", quality="high", overwrite=True,
                           timeout=self.timeout)
            warnings += res.get("warnings") or []
            mcp.on_progress = None
            png = work_dir / f"{out_video.stem}.cover.png"
            canvas = plan["canvas"]
            mcp.call("render_frame", time=float(plan.get("cover", {}).get("time", 1.0)),
                     width=int(canvas["width"]), save_path=str(png))
            out_cover.parent.mkdir(parents=True, exist_ok=True)
            F.run([*F.ffmpeg_base(), "-i", str(png), "-q:v", "2", str(out_cover)], timeout=60, what="cover")
            png.unlink(missing_ok=True)
            mcp.call("close_project")
        finally:
            mcp.close()
        if not out_video.exists() or out_video.stat().st_size == 0:
            raise RenderError("剪映 me export produced no file")
        return RenderResult(video=out_video, cover=out_cover, backend=self.name, seconds=time.monotonic() - t0,
                            editor_project=proj_path, warnings=warnings)


def build_timeline(mcp: McpClient, plan: dict, proj_path: Path, warnings: list[str],
                   progress: Progress | None = None) -> None:
    canvas = plan["canvas"]
    mcp.call("create_project", name=proj_path.stem, path=str(proj_path), width=int(canvas["width"]),
             height=int(canvas["height"]), fps=str(canvas.get("fps", 30)))
    clips = plan["clips"]
    bgm = (plan.get("audio") or {}).get("bgm")
    paths = list(dict.fromkeys([c["path"] for c in clips] + ([bgm["path"]] if bgm else [])))
    imp = mcp.call("import_media", paths=paths, timeout=600)
    if imp.get("errors"):
        raise RenderError(f"import failed: {imp['errors']}")
    by_path = {str(Path(r["path"]).resolve()): r["id"] for r in imp["imported"]}
    for r in imp["imported"]:
        warnings += r.get("warnings") or []
    rid = lambda p: by_path[str(Path(p).resolve())]  # noqa: E731
    if progress:
        progress(0.1, "building timeline")

    seq = mcp.call("append_clips", items=[{"resource_id": rid(c["path"]), "source_in": c["src_in"],
                                           "duration": round(c["src_out"] - c["src_in"], 3)} for c in clips])
    ids = [c["id"] for c in seq["clips"]]
    for cid, c in zip(ids, clips):
        tr = c.get("transform") or {}
        upd = {"x": tr.get("x", 0.5), "y": tr.get("y", 0.5), "scale": tr.get("scale", 1.0),
               "volume_db": c.get("volume_db", 0.0)}
        if abs(c.get("speed", 1.0) - 1.0) > 1e-3:
            upd["speed"] = c["speed"]
        mcp.call("update_clip", clip_id=cid, **upd)
        if c.get("background") == "blur":
            mcp.call("set_clip_background", clip_id=cid, type="blur", blur=0.6)
        if (c.get("color") or {}).get("filter"):
            mcp.call("set_clip_color", clip_id=cid, filter=c["color"]["filter"])
        anim = c.get("animation") or {}
        if anim.get("combo"):
            mcp.call("set_clip_animation", clip_id=cid, combo=anim["combo"])
        elif anim.get("in"):
            mcp.call("set_clip_animation", clip_id=cid, **{"in": anim["in"], "in_duration": 0.4})
    for cid, c in zip(ids, clips):
        tr = c.get("transition_out")
        if tr:
            try:
                mcp.call("add_transition", kind=tr["kind"], from_clip_id=cid, duration=tr["duration"])
            except RenderError as e:  # e.g. clip too short after speed change: keep the hard cut
                warnings.append(f"transition {tr['kind']} skipped: {e}")
    for t in plan.get("texts", []):
        mcp.call("add_text", text=t["text"], start=t["start"], duration=t["duration"], preset=t.get("preset"),
                 style=t.get("style") or {}, anim_in=t.get("anim_in"), anim_out=t.get("anim_out"))
    if bgm:
        total = sum(c["out_duration"] for c in clips)
        b = mcp.call("add_clip", resource_id=rid(bgm["path"]), new_track=True, stream="audio", start=0.0,
                     duration=round(total, 3), source_in=bgm.get("src_in", 0.0))
        mcp.call("update_clip", clip_id=b["id"], volume_db=bgm.get("volume_db", -9.0), fade_in=0.3, fade_out=1.2)
    tl = mcp.call("get_timeline")
    want = round(sum(c["out_duration"] for c in clips), 2)
    if abs(float(tl.get("duration", want)) - want) > 0.25:
        warnings.append(f"timeline duration {tl.get('duration')}s differs from plan {want}s")
