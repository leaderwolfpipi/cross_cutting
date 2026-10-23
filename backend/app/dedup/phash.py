"""Mirror-invariant perceptual frame hash (64 bit) and vectorised Hamming distance.

hash(frame) = sign(DCT(sym(frame))[low freqs] - median), where sym(f) = (f + fliplr(f)) / 2.
A horizontally flipped frame has the same symmetrised image, so its hash is identical; the DCT of a symmetric
image has zero odd horizontal frequencies, so only even columns are used. Grey-level DCT signs relative to the
median are robust to brightness / contrast / hue grading and re-encoding.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import cv2
import numpy as np

from ..media import ffmpeg as F

N = 32
FRAME_STEP = 0.25  # seconds between sampled frames (dense enough for 0.8–1.25× speed changes on handheld footage)
# every frame is also hashed as crops (scale, horizontal anchor 0=left .5=centre 1=right), so a punched-in or
# re-framed reuse still matches; matching is done in both directions (see matcher.Ref.distances)
CROPS = ((1.0, 0.5), (0.87, 0.5), (0.77, 0.5), (0.67, 0.5), (0.77, 0.0), (0.77, 1.0), (0.67, 0.15), (0.67, 0.85))
SCALES = CROPS  # backwards-compatible name: number of hash columns per frame
INFO_STD = 10.0  # frames flatter than this (black / solid colour) carry no identity


def _dct_matrix(n: int) -> np.ndarray:
    k = np.arange(n)[:, None]
    i = np.arange(n)[None, :]
    c = np.cos(np.pi * (2 * i + 1) * k / (2 * n)) * np.sqrt(2.0 / n)
    c[0] /= np.sqrt(2.0)
    return c.astype(np.float64)


_C = _dct_matrix(N)
# 64 lowest even-column coefficients ordered by frequency, DC excluded
_COEFS = sorted(((r, c) for r in range(10) for c in range(0, 20, 2) if (r, c) != (0, 0)),
                key=lambda rc: (rc[0] + rc[1] // 2, rc[0]))[:64]
_ROWS = np.array([r for r, _ in _COEFS])
_COLS = np.array([c for _, c in _COEFS])
_POP = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def hash_frames(gray32: np.ndarray) -> np.ndarray:
    """gray32: (n, 32, 32) float array → (n,) uint64 hashes."""
    g = gray32.astype(np.float64)
    g = (g + g[:, :, ::-1]) * 0.5
    d = np.einsum("ij,njk,lk->nil", _C, g, _C)
    coef = d[:, _ROWS, _COLS]  # (n, 64)
    bits = coef > np.median(coef, axis=1, keepdims=True)
    packed = np.ascontiguousarray(np.packbits(bits, axis=1))  # (n, 8) uint8, big-endian bit order
    return packed.view(">u8").reshape(-1).astype(np.uint64)


def to_gray32(gray: np.ndarray) -> np.ndarray:
    return cv2.resize(gray, (N, N), interpolation=cv2.INTER_AREA)


def crops(gray: np.ndarray) -> list[np.ndarray]:
    h, w = gray.shape
    out = []
    for s, ax in CROPS:
        ch, cw = max(8, int(round(h * s))), max(8, int(round(w * s)))
        y, x = (h - ch) // 2, int(round((w - cw) * ax))
        out.append(to_gray32(gray[y:y + ch, x:x + cw]))
    return out


def video_hashes(path: str | Path, step: float = FRAME_STEP) -> tuple[np.ndarray, np.ndarray]:
    """Sample a video every `step` seconds → ((n, len(SCALES)) uint64 hashes, (n,) uint8 informative mask).
    Column 0 is the full frame (used for queries); all columns are used when the video is a reference."""
    frames, info = [], []
    for _, g in F.iter_frames(path, 1.0 / step, 96, 96, gray=True):
        info.append(1 if float(g.std()) > INFO_STD else 0)
        frames.extend(crops(g))
    if not frames:
        return np.zeros((0, len(SCALES)), np.uint64), np.zeros(0, np.uint8)
    return hash_frames(np.stack(frames)).reshape(-1, len(SCALES)), np.array(info, np.uint8)


def hamming_matrix(a: np.ndarray, b: np.ndarray, chunk: int = 50_000) -> np.ndarray:
    """(len(a), len(b)) uint8 matrix of Hamming distances between uint64 hash arrays."""
    a = np.ascontiguousarray(a, dtype=np.uint64)
    b = np.ascontiguousarray(b, dtype=np.uint64)
    out = np.empty((len(a), len(b)), np.uint8)
    for s in range(0, len(b), chunk):
        x = a[:, None] ^ b[None, s:s + chunk]
        out[:, s:s + chunk] = _POP[x.view(np.uint8).reshape(*x.shape, 8)].sum(axis=2, dtype=np.uint8)
    return out


def hamming(a: int, b: int) -> int:
    return int(a ^ b).bit_count()


def pack(arr: np.ndarray, dtype: str) -> bytes:
    return np.asarray(arr, dtype=dtype).tobytes()


def unpack(blob: bytes | None, dtype: str) -> np.ndarray:
    return np.frombuffer(blob or b"", dtype=dtype)


def concat(arrays: Iterable[np.ndarray], dtype) -> np.ndarray:
    arrays = list(arrays)
    return np.concatenate(arrays).astype(dtype) if arrays else np.zeros(0, dtype)
