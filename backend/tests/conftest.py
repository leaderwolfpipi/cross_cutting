from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
EXAMPLES = BACKEND.parent.parent / "examples"


@pytest.fixture(scope="session", autouse=True)
def isolated_settings(tmp_path_factory):
    data = tmp_path_factory.mktemp("data")
    os.environ.update({"DATA_DIR": str(data), "DATABASE_URL": "", "API_KEYS": "test-key", "SECRET_KEY": "t" * 40,
                       "RENDER_BACKEND": "ffmpeg", "EMBEDDED_WORKER": "false", "COPYWRITER": "template"})
    from app import config, db

    config.get_settings.cache_clear()
    db.reset_engine()
    db.init_db()
    yield config.get_settings()


@pytest.fixture()
def settings(isolated_settings):
    return isolated_settings


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def clips(tmp_path_factory) -> dict[str, Path]:
    """Synthetic test videos: a textured moving scene, its transforms, and unrelated scenes."""
    d = tmp_path_factory.mktemp("clips")
    out = {}
    base = d / "base.mp4"
    ffmpeg("-f", "lavfi", "-i", "mandelbrot=s=540x960:r=30:start_scale=3:end_scale=0.4", "-f", "lavfi", "-i",
           "anoisesrc=d=8:c=brown:r=48000:a=0.5:seed=7,tremolo=f=3:d=0.7", "-t", "8", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-shortest", str(base))
    out["base"] = base
    for name, vf, af in [("mirror", "hflip", "anull"), ("grade", "eq=saturation=1.4:brightness=0.06,hue=h=15", "volume=0.7"),
                         ("speed", "setpts=PTS/1.1", "atempo=1.1"), ("crop", "crop=iw*0.88:ih*0.88,scale=540:960", "anull")]:
        p = d / f"{name}.mp4"
        ffmpeg("-i", str(base), "-vf", vf, "-af", af, "-c:v", "libx264", "-crf", "20", "-c:a", "aac", str(p))
        out[name] = p
    for name, src in [("other1", "life=s=540x960:mold=10:r=30:ratio=0.1:death_color=#C83232:life_color=#00ff00"),
                      ("other2", "cellauto=s=540x960:rule=110:r=30"),
                      ("other3", "testsrc2=s=540x960:r=30")]:
        p = d / f"{name}.mp4"
        ffmpeg("-f", "lavfi", "-i", src, "-f", "lavfi", "-i", "anoisesrc=d=8:c=pink:r=48000:a=0.3", "-t", "8",
               "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p))
        out[name] = p
    cover = d / "cover.jpg"
    ffmpeg("-i", str(base), "-frames:v", "1", str(cover))
    out["cover"] = cover
    return out


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def mock_platform():
    """Run the TEST-ONLY mock platform server in a background thread."""
    import uvicorn

    from mock_platforms import server

    server.REVIEW_S = 0.6
    server.HANG_S = 3.0
    port = free_port()
    cfg = uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(cfg)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield {"base": f"http://127.0.0.1:{port}", "module": server}
    srv.should_exit = True


@pytest.fixture()
def examples_dir():
    if not EXAMPLES.is_dir() or not any(EXAMPLES.rglob("*.MP4")):
        pytest.skip("examples/ footage not available")
    return EXAMPLES


def have(binary: str) -> bool:
    return shutil.which(binary) is not None
