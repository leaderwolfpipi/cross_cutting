"""Small beat tracker for BGM (spectral-flux onsets → tempo by autocorrelation → best-phase beat grid)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import ffmpeg as F

SR = 22050
HOP = 512


def onset_envelope(pcm: np.ndarray) -> np.ndarray:
    n = 1 + max(0, len(pcm) - 2048) // HOP
    if n < 4:
        return np.zeros(0)
    idx = np.arange(2048)[None, :] + HOP * np.arange(n)[:, None]
    spec = np.log1p(np.abs(np.fft.rfft(pcm[idx] * np.hanning(2048)[None, :], axis=1)))
    flux = np.maximum(0.0, np.diff(spec, axis=0)).sum(axis=1)
    flux -= np.convolve(flux, np.ones(16) / 16, mode="same")  # local mean removal
    return np.maximum(flux, 0.0)


def track(path: str | Path, max_seconds: float = 240.0) -> tuple[float, list[float]]:
    """Return (bpm, beat times in seconds). (0, []) for silence / very short audio."""
    pcm = F.read_audio(path, SR, max_seconds)
    env = onset_envelope(pcm)
    if len(env) < 64 or env.max() <= 0:
        return 0.0, []
    fps = SR / HOP
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    lags = np.arange(len(ac))
    lo, hi = int(fps * 60 / 180), int(fps * 60 / 70)  # 70–180 bpm
    if hi >= len(ac):
        return 0.0, []
    weight = np.exp(-0.5 * (np.log2(lags[lo:hi] / (fps * 60 / 120))) ** 2)  # mild prior around 120 bpm
    period = lo + int(np.argmax(ac[lo:hi] * weight))
    phase = int(np.argmax([env[p::period].sum() for p in range(period)]))
    beats = [round((phase + k * period) / fps, 3) for k in range((len(env) - phase) // period + 1)]
    return round(60.0 * fps / period, 1), beats
