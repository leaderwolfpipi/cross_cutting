"""FFmpeg / FFprobe helpers. Every call is an argument list (no shell), bounded by a timeout, and raises
MediaError with the tail of stderr on failure."""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..config import get_settings

log = logging.getLogger(__name__)


class MediaError(RuntimeError):
    pass


def run(args: list[str], timeout: float = 1800, what: str = "ffmpeg") -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise MediaError(f"{what} timed out after {timeout:.0f}s") from e
    except FileNotFoundError as e:
        raise MediaError(f"{args[0]} not found: install FFmpeg or set FFMPEG/FFPROBE") from e
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-12:]
        raise MediaError(f"{what} failed ({proc.returncode}): " + " | ".join(tail))
    return proc


def ffmpeg_base() -> list[str]:
    return [get_settings().ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]


# ====================================================================== probe
@dataclass
class ProbeInfo:
    duration: float
    width: int  # display size, rotation applied
    height: int
    fps: float
    codec: str
    pix_fmt: str
    rotation: int
    hdr: str  # "" | "hlg" | "pq"
    has_audio: bool
    video_index: int  # absolute stream index of the primary video stream

    def to_dict(self) -> dict:
        return asdict(self)


def _rate(text: str | None) -> float:
    try:
        num, den = (text or "0/1").split("/")
        return float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path: str | Path) -> ProbeInfo:
    out = run([get_settings().ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
               str(path)], timeout=60, what="ffprobe").stdout
    data = json.loads(out)
    streams = data.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic") and s.get("codec_name") not in ("mjpeg", "png")]
    if not videos:
        raise MediaError("no video stream")
    # primary = largest picture (DJI files carry a second low-res stream and an MJPEG cover)
    v = max(videos, key=lambda s: (int(s.get("width", 0)) * int(s.get("height", 0)), -int(s["index"])))
    rotation = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(round(float(sd["rotation"])))
    if not rotation and "rotate" in (v.get("tags") or {}):
        rotation = -int(v["tags"]["rotate"])
    w, h = int(v.get("width", 0)), int(v.get("height", 0))
    if abs(rotation) % 180 == 90:
        w, h = h, w
    trc = v.get("color_transfer", "")
    hdr = "hlg" if trc == "arib-std-b67" else "pq" if trc == "smpte2084" else ""
    duration = float(data.get("format", {}).get("duration") or v.get("duration") or 0)
    fps = _rate(v.get("avg_frame_rate")) or _rate(v.get("r_frame_rate"))
    return ProbeInfo(duration=duration, width=w, height=h, fps=round(fps, 3), codec=v.get("codec_name", ""),
                     pix_fmt=v.get("pix_fmt", ""), rotation=rotation, hdr=hdr,
                     has_audio=any(s.get("codec_type") == "audio" for s in streams), video_index=int(v["index"]))


# ====================================================================== capabilities
@lru_cache
def has_filter(name: str) -> bool:
    try:
        out = run([get_settings().ffmpeg, "-hide_banner", "-filters"], timeout=30).stdout.decode()
    except MediaError:
        return False
    return any(line.split()[1:2] == [name] for line in out.splitlines() if len(line.split()) > 1)


@lru_cache
def opencl_tonemap_ok() -> bool:
    """tonemap_opencl needs a working OpenCL device; test it once on a synthetic HLG-tagged frame."""
    if not has_filter("tonemap_opencl"):
        return False
    args = [*ffmpeg_base(), "-init_hw_device", "opencl=gpu", "-filter_hw_device", "gpu", "-f", "lavfi", "-i",
            "testsrc2=s=64x64:d=0.1", "-vf",
            "format=yuv420p10le,setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc,"
            "format=p010,hwupload,tonemap_opencl=tonemap=hable:desat=0:t=bt709:p=bt709:m=bt709:format=nv12,"
            "hwdownload,format=nv12", "-f", "null", "-"]
    try:
        run(args, timeout=60)
        return True
    except MediaError as e:
        log.info("tonemap_opencl unavailable, using colorspace approximation: %s", e)
        return False


@lru_cache
def hwaccel_args() -> tuple[str, ...]:
    mode = get_settings().hwaccel
    if mode == "none":
        return ()
    if mode in ("auto", "videotoolbox"):
        try:
            out = run([get_settings().ffmpeg, "-hide_banner", "-hwaccels"], timeout=30).stdout.decode()
        except MediaError:
            return ()
        if "videotoolbox" in out:
            return ("-hwaccel", "videotoolbox")
    return ()


# ====================================================================== normalisation
def even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def proxy_size(info: ProbeInfo, short_side: int) -> tuple[int, int]:
    s = min(info.width, info.height)
    if s <= short_side:
        return even(info.width), even(info.height)
    k = short_side / s
    return even(info.width * k), even(info.height * k)


def make_proxy(src: Path, dst: Path, info: ProbeInfo) -> tuple[int, int]:
    """Normalise a camera file once: primary video stream, rotation baked, HDR→SDR BT.709, 8-bit 4:2:0, 30 fps,
    short side ≤ proxy_short_side, AAC stereo 48 kHz (silence when the source has none)."""
    s = get_settings()
    w, h = proxy_size(info, s.proxy_short_side)
    pre: list[str] = []
    if info.hdr and opencl_tonemap_ok():
        pre = ["-init_hw_device", "opencl=gpu", "-filter_hw_device", "gpu"]
        vf = ("format=p010,hwupload,tonemap_opencl=tonemap=hable:desat=0:t=bt709:p=bt709:m=bt709:format=nv12,"
              f"hwdownload,format=nv12,scale={w}:{h}:flags=lanczos")
    elif info.hdr:
        vf = f"colorspace=all=bt709:iall=bt2020:itrc=bt2020-10:format=yuv420p,scale={w}:{h}:flags=lanczos"
    else:
        vf = f"scale={w}:{h}:flags=lanczos"
    vf += ",fps=30,format=yuv420p,setsar=1"
    args = [*ffmpeg_base(), *pre, *hwaccel_args(), "-i", str(src)]
    if not info.has_audio:
        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    args += ["-map", f"0:{info.video_index}", "-map", "0:a:0" if info.has_audio else "1:a:0",
             "-vf", vf, "-c:v", "libx264", "-preset", "fast", "-crf", str(s.proxy_crf), "-g", "30",
             "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
             "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-shortest",
             "-map_metadata", "-1", "-movflags", "+faststart", str(dst)]
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        run(args, timeout=max(600, info.duration * 20), what="proxy")
    except MediaError:
        if not hwaccel_args():
            raise
        # some files fail with hardware decode (odd profiles): retry in software once
        log.warning("hw decode failed for %s, retrying in software", src)
        run([a for a in args if a not in hwaccel_args()], timeout=max(600, info.duration * 40), what="proxy")
    return w, h


# ====================================================================== readers
def iter_frames(path: str | Path, fps: float, width: int, height: int, start: float = 0.0,
                duration: float | None = None, gray: bool = False) -> Iterator[tuple[float, np.ndarray]]:
    """Decode frames at `fps`, scaled to width×height, as uint8 arrays (BGR or gray). Yields (time, frame)."""
    pix, ch = ("gray", 1) if gray else ("bgr24", 3)
    args = [*ffmpeg_base()]
    if start > 0:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(path)]
    if duration is not None:
        args += ["-t", f"{duration:.3f}"]
    args += ["-map", "0:v:0", "-vf", f"fps={fps},scale={width}:{height}:flags=area", "-f", "rawvideo",
             "-pix_fmt", pix, "-"]
    size = width * height * ch
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        i = 0
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            frame = np.frombuffer(buf, np.uint8).reshape((height, width) if gray else (height, width, ch))
            yield start + i / fps, frame
            i += 1
    finally:
        if proc.stdout:
            proc.stdout.close()
        proc.kill()
        proc.wait()


def read_audio(path: str | Path, sr: int, max_seconds: float | None = None) -> np.ndarray:
    """Mono float32 PCM in [-1, 1]; empty array when the file has no audio."""
    args = [*ffmpeg_base(), "-i", str(path)]
    if max_seconds:
        args += ["-t", f"{max_seconds:.3f}"]
    args += ["-map", "0:a:0?", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    out = run(args, timeout=600, what="audio decode").stdout
    return np.frombuffer(out, "<i2").astype(np.float32) / 32768.0


def extract_frame(path: str | Path, t: float, dst: Path, width: int = 480) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    run([*ffmpeg_base(), "-ss", f"{max(0.0, t):.3f}", "-i", str(path), "-map", "0:v:0", "-frames:v", "1",
         "-vf", f"scale={width}:-2", "-q:v", "3", str(dst)], timeout=60, what="thumbnail")
    return dst


def loudness_lufs(path: str | Path) -> float | None:
    proc = subprocess.run([get_settings().ffmpeg, "-hide_banner", "-nostdin", "-i", str(path), "-map", "0:a:0?",
                           "-af", "ebur128=framelog=quiet", "-f", "null", "-"], capture_output=True, timeout=600)
    text = proc.stderr.decode("utf-8", "replace")
    idx = text.rfind("Integrated loudness:")
    if idx < 0:
        return None
    for line in text[idx:].splitlines():
        line = line.strip()
        if line.startswith("I:"):
            try:
                return float(line.split()[1])
            except (IndexError, ValueError):
                return None
    return None
