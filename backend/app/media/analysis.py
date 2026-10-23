"""Material analysis on the normalised proxy: shot boundaries (PySceneDetect) and per-frame features that the
planner uses to cast clips into narrative roles."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from . import ffmpeg as F

log = logging.getLogger(__name__)

FEATURE_WIDTH = 192
MIN_SHOT_S = 0.6


@dataclass
class Analysis:
    fps: float
    duration: float
    motion: list[float] = field(default_factory=list)  # mean |Δgray| between sampled frames, 0..1
    sharp: list[float] = field(default_factory=list)  # 0..1 (log-scaled Laplacian variance)
    sat: list[float] = field(default_factory=list)  # mean saturation 0..1
    warm: list[float] = field(default_factory=list)  # share of saturated red/orange pixels (cooked / red seafood)
    bright: list[float] = field(default_factory=list)  # mean value 0..1
    info: list[int] = field(default_factory=list)  # 1 = frame has enough texture to be meaningful
    shots: list[tuple[float, float]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("fps", "duration", "motion", "sharp", "sat", "warm", "bright", "info")}


def frame_features(prev_gray: np.ndarray | None, bgr: np.ndarray) -> tuple[float, float, float, float, float, int]:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    motion = float(np.mean(cv2.absdiff(gray, prev_gray))) / 255.0 if prev_gray is not None else 0.0
    lap = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    sharp = min(1.0, np.log1p(lap) / np.log1p(2500.0))
    warm_mask = ((h < 18) | (h > 165)) & (s > 90) & (v > 60)
    info = int(float(gray.std()) > 12.0)
    return motion, sharp, float(s.mean()) / 255.0, float(warm_mask.mean()), float(v.mean()) / 255.0, info


def analyse(proxy: Path, info: F.ProbeInfo, fps: float = 2.0) -> Analysis:
    w = FEATURE_WIDTH
    h = F.even(w * info.height / max(1, info.width))
    a = Analysis(fps=fps, duration=info.duration)
    prev = None
    for _, frame in F.iter_frames(proxy, fps, w, h):
        m, sh, sa, wa, br, inf = frame_features(prev, frame)
        a.motion.append(round(m, 4))
        a.sharp.append(round(sh, 4))
        a.sat.append(round(sa, 4))
        a.warm.append(round(wa, 4))
        a.bright.append(round(br, 4))
        a.info.append(inf)
        prev = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if len(a.motion) > 1:
        a.motion[0] = a.motion[1]
    a.shots = detect_shots(proxy, info.duration)
    return a


def detect_shots(proxy: Path, duration: float) -> list[tuple[float, float]]:
    """Content-based cut detection; one shot covering the file when nothing is found or detection fails."""
    try:
        from scenedetect import ContentDetector, detect

        scenes = detect(str(proxy), ContentDetector(threshold=27.0, min_scene_len=int(MIN_SHOT_S * 30)))
        shots = [(s.get_seconds(), e.get_seconds()) for s, e in scenes]
    except Exception as e:  # noqa: BLE001 - detection is best effort, the planner works on whole files too
        log.warning("shot detection failed for %s: %s", proxy, e)
        shots = []
    shots = [(round(s, 3), round(min(e, duration), 3)) for s, e in shots if e - s >= MIN_SHOT_S]
    return shots or [(0.0, round(duration, 3))]


# ---------------------------------------------------------------------- window statistics used by the planner
def window_stats(an: dict, start: float, end: float) -> dict[str, float]:
    fps = an.get("fps", 2.0)
    i0, i1 = int(start * fps), max(int(start * fps) + 1, int(np.ceil(end * fps)))
    def mean(key: str) -> float:
        arr = an.get(key) or []
        seg = arr[i0:i1] or arr[-1:] or [0.0]
        return float(np.mean(seg))
    motion, sharp, sat, warm, bright = (mean(k) for k in ("motion", "sharp", "sat", "warm", "bright"))
    info = mean("info") if an.get("info") else 1.0
    vivid = min(1.0, 0.55 * min(1.0, sat / 0.55) + 0.45 * min(1.0, warm / 0.25))
    motion_n = min(1.0, motion / 0.06)
    exposure = 1.0 - min(1.0, abs(bright - 0.5) / 0.5) ** 2
    eye = 0.35 * motion_n + 0.35 * vivid + 0.2 * sharp + 0.1 * exposure
    return {"motion": motion_n, "sharp": sharp, "vivid": vivid, "bright": bright, "exposure": exposure,
            "info": info, "eye_catch": eye}
