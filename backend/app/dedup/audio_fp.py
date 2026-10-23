"""Philips-style (Haitsma & Kalker) audio fingerprint: 32-bit sub-fingerprints from the sign of energy differences
between 33 log-spaced bands across adjacent frames. Silent frames are encoded as 0 and ignored when matching."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from ..media import ffmpeg as F

SR = 5512
FRAME = 2048
HOP = 128
STEP = HOP / SR  # ≈ 23 ms
_EDGES_HZ = np.geomspace(300.0, 2000.0, 34)
_BIN_HZ = SR / FRAME
_BAND_BINS = [(int(lo / _BIN_HZ), max(int(lo / _BIN_HZ) + 1, int(hi / _BIN_HZ)))
              for lo, hi in zip(_EDGES_HZ[:-1], _EDGES_HZ[1:])]
_POP32 = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)
SILENCE_RMS = 0.003


def fingerprint_pcm(pcm: np.ndarray) -> np.ndarray:
    if len(pcm) < FRAME + HOP:
        return np.zeros(0, np.uint32)
    n = 1 + (len(pcm) - FRAME) // HOP
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    frames = pcm[idx] * np.hanning(FRAME)[None, :]
    spec = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    energy = np.stack([spec[:, a:b].sum(axis=1) for a, b in _BAND_BINS], axis=1)  # (n, 33)
    diff_band = energy[:, :-1] - energy[:, 1:]  # (n, 32)
    bits = (diff_band[1:] - diff_band[:-1]) > 0  # (n-1, 32)
    packed = np.ascontiguousarray(np.packbits(bits, axis=1)).view(">u4").reshape(-1).astype(np.uint32)
    rms = np.sqrt(np.mean(pcm[idx[1:]] ** 2, axis=1))
    packed[rms < SILENCE_RMS] = 0
    return packed


def fingerprint_file(path: str | Path) -> np.ndarray:
    return fingerprint_pcm(F.read_audio(path, SR))


def _ber(a: np.ndarray, b: np.ndarray) -> float:
    x = (a ^ b).view(np.uint8)
    return float(_POP32[x].sum()) / (32.0 * len(a))


def coverage(query: np.ndarray, ref: np.ndarray, block_s: float = 2.0, max_ber: float = 0.35) -> float:
    """Share of the query's audible 2 s blocks whose best-aligned BER against `ref` is below max_ber."""
    if len(query) == 0 or len(ref) == 0:
        return 0.0
    positions: dict[int, list[int]] = defaultdict(list)
    for j, v in enumerate(ref.tolist()):
        if v:
            positions[v].append(j)
    blk = max(8, int(block_s / STEP))
    audible = matched = 0
    for s in range(0, len(query) - blk + 1, blk):
        q = query[s:s + blk]
        live = q != 0
        if live.mean() < 0.5:
            continue
        audible += 1
        votes: Counter[int] = Counter()
        for i, v in enumerate(q.tolist()):
            if v:
                for j in positions.get(v, ()):
                    votes[j - (s + i)] += 1
        for off, _ in votes.most_common(3):
            a0, b0 = s, s + off
            lo = max(0, -b0)
            hi = min(blk, len(ref) - b0)
            if hi - lo < blk // 2:
                continue
            qa, rb = query[a0 + lo:a0 + hi], ref[b0 + lo:b0 + hi]
            mask = (qa != 0) & (rb != 0)
            if mask.sum() >= blk // 3 and _ber(qa[mask], rb[mask]) < max_ber:
                matched += 1
                break
    return matched / audible if audible else 0.0
