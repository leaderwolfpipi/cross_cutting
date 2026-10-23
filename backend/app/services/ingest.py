"""Material / history / BGM ingestion jobs."""

from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import get_settings
from ..dedup import engine as dedup
from ..media import analysis as A
from ..media import beats
from ..media import ffmpeg as F
from ..models import BgmTrack, HistoryItem, Material, Shot

log = logging.getLogger(__name__)
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".webm"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def store_upload(tmp: Path, original_name: str, kind: str) -> Path:
    """Move an uploaded temp file into the data dir under a server-chosen name (never the client's path)."""
    ext = Path(original_name).suffix.lower()
    allowed = AUDIO_EXT if kind == "bgm" else VIDEO_EXT
    if ext not in allowed:
        raise ValueError(f"unsupported file type {ext or '(none)'}")
    dst = get_settings().path(kind) / f"{uuid.uuid4().hex}{ext}"
    shutil.move(str(tmp), dst)
    return dst


def material_file(m: Material) -> Path:
    p = Path(m.storage_path)
    return p if p.is_absolute() else get_settings().abs(m.storage_path)


def analyze_material(db: Session, material: Material, progress=None) -> None:
    s = get_settings()
    src = material_file(material)
    material.status, material.error = "analyzing", ""
    db.commit()
    info = F.probe(src)
    material.duration, material.width, material.height = info.duration, info.width, info.height
    material.fps, material.codec, material.has_audio, material.hdr = info.fps, info.codec, info.has_audio, info.hdr
    if progress:
        progress(0.1, "normalising proxy")
    proxy = s.path("proxies") / f"{material.id}.mp4"
    F.make_proxy(src, proxy, info)
    material.proxy_path = s.rel(proxy)
    if progress:
        progress(0.6, "analysing shots")
    pinfo = F.probe(proxy)
    an = A.analyse(proxy, pinfo, s.analysis_fps)
    material.analysis = an.to_dict()
    for old in list(material.shots):
        db.delete(old)
    db.flush()
    thumbs = s.path("thumbs")
    for i, (a, b) in enumerate(an.shots):
        st = A.window_stats(material.analysis, a, b)
        thumb = F.extract_frame(proxy, (a + b) / 2, thumbs / f"{material.id}_{i}.jpg", 320)
        db.add(Shot(material_id=material.id, idx=i, start=a, end=b, motion=round(st["motion"], 3),
                    sharpness=round(st["sharp"], 3), vivid=round(st["vivid"], 3), eye_catch=round(st["eye_catch"], 3),
                    thumb_path=s.rel(thumb)))
    material.thumb_path = s.rel(F.extract_frame(proxy, info.duration / 2, thumbs / f"{material.id}.jpg", 360))
    if progress:
        progress(0.85, "fingerprinting")
    fp = dedup.compute(proxy, with_audio=False)
    dedup.save(db, "material", material.id, fp)
    dedup.assign_source_group(db, material, fp)
    material.status = "ready"
    db.commit()


def analyze_history(db: Session, item: HistoryItem, progress=None) -> None:
    path = get_settings().abs(item.storage_path)
    info = F.probe(path)
    item.duration = info.duration
    if progress:
        progress(0.2, "fingerprinting")
    dedup.save(db, "history", item.id, dedup.compute(path), platform=item.platform)
    item.status, item.error = "ready", ""
    db.commit()


def analyze_bgm(db: Session, track: BgmTrack, progress=None) -> None:
    path = get_settings().abs(track.storage_path)
    out = F.run([get_settings().ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                 str(path)], timeout=60, what="ffprobe").stdout.decode().strip()
    track.duration = float(out or 0)
    track.bpm, track.beats = beats.track(path)
    db.commit()
