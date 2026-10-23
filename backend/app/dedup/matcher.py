"""Temporal visual coverage between a query video and reference videos, from frame-hash matches.

A query frame counts as covered by a reference only when the match is temporally consistent: a neighbouring
query frame (±1..2 samples) also matches the same reference near the corresponding position, moving forward at
a plausible speed (0.5–2× in sample units, which allows the 0.8–1.25× speed changes editors apply plus
sampling jitter). Isolated coincidental matches — e.g. two different shots of the same tank — do not count.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .phash import FRAME_STEP, hamming_matrix

_NEIGHBOURS = [(di, dj) for di in (-2, -1, 1, 2) for dj in range(-4, 5)
               if dj != 0 and np.sign(dj) == np.sign(di) and 0.5 * abs(di) <= abs(dj) <= 2 * abs(di)]


@dataclass
class Ref:
    owner_type: str
    owner_id: str
    hashes: np.ndarray  # (m, len(CROPS)) multi-crop hashes, or (m,) full-frame only
    info: np.ndarray
    step: float = FRAME_STEP

    def distances(self, query: np.ndarray) -> np.ndarray:
        """(n, m) Hamming distance between query and reference frames, minimised over crops in both directions:
        full query vs every reference crop (query is a zoomed-in reuse) and every query crop vs the full reference
        (the reference is the zoomed-in one)."""
        q = query if query.ndim == 2 else query[:, None]
        r = self.hashes if self.hashes.ndim == 2 else self.hashes[:, None]
        n, m = len(q), len(r)
        d1 = hamming_matrix(q[:, 0], r.reshape(-1)).reshape(n, m, r.shape[1]).min(axis=2)
        d2 = hamming_matrix(q.reshape(-1), r[:, 0]).reshape(n, q.shape[1], m).min(axis=1)
        return np.minimum(d1, d2)


@dataclass
class PairResult:
    owner_type: str
    owner_id: str
    coverage: float
    segments: list[dict] = field(default_factory=list)  # [{q0,q1,r0,r1}] seconds


def _consistent(match: np.ndarray) -> np.ndarray:
    """match: (n, m) bool → (n, m) bool where each match has a temporally consistent neighbour."""
    n, m = match.shape
    ok = np.zeros_like(match)
    for di, dj in _NEIGHBOURS:
        shifted = np.zeros_like(match)
        i0, i1 = max(0, -di), min(n, n - di)
        j0, j1 = max(0, -dj), min(m, m - dj)
        if i1 <= i0 or j1 <= j0:
            continue
        shifted[i0:i1, j0:j1] = match[i0 + di:i1 + di, j0 + dj:j1 + dj]
        ok |= match & shifted
    return ok


def _segments(rows: np.ndarray, cols: np.ndarray, step_q: float, step_r: float) -> list[dict]:
    if len(rows) == 0:
        return []
    order = np.argsort(rows)
    rows, cols = rows[order], cols[order]
    segs, start = [], 0
    for k in range(1, len(rows) + 1):
        if k == len(rows) or rows[k] - rows[k - 1] > 2:
            r, c = rows[start:k], cols[start:k]
            segs.append({"q0": round(float(r.min()) * step_q, 2), "q1": round(float(r.max() + 1) * step_q, 2),
                         "r0": round(float(c.min()) * step_r, 2), "r1": round(float(c.max() + 1) * step_r, 2)})
            start = k
    return segs


def coverage_against(q_hashes: np.ndarray, q_info: np.ndarray, refs: list[Ref], max_dist: int = 10,
                     q_step: float = FRAME_STEP) -> tuple[list[PairResult], float]:
    """Pairwise coverage per reference plus library coverage (query frames covered by any reference).
    q_hashes / ref hashes are (n, len(CROPS)) multi-crop arrays (column 0 = full frame) or (n,) full frames."""
    live = q_info.astype(bool) if len(q_info) == len(q_hashes) else np.ones(len(q_hashes), bool)
    n_live = int(live.sum())
    if n_live == 0 or not refs:
        return [PairResult(r.owner_type, r.owner_id, 0.0) for r in refs], 0.0
    covered_any = np.zeros(len(q_hashes), bool)
    results = []
    for ref in refs:
        if len(ref.hashes) == 0:
            results.append(PairResult(ref.owner_type, ref.owner_id, 0.0))
            continue
        r_live = ref.info.astype(bool) if len(ref.info) == len(ref.hashes) else np.ones(len(ref.hashes), bool)
        match = (ref.distances(q_hashes) <= max_dist) & live[:, None] & r_live[None, :]
        ok = _consistent(match)
        covered = ok.any(axis=1)
        covered_any |= covered
        rows, cols = np.nonzero(ok)
        # keep, per query frame, its best-aligned reference column for the explanation
        best = {}
        for r_, c_ in zip(rows.tolist(), cols.tolist()):
            best.setdefault(r_, c_)
        br = np.array(list(best.keys()), dtype=int)
        bc = np.array(list(best.values()), dtype=int)
        results.append(PairResult(ref.owner_type, ref.owner_id, round(float(covered[live].mean()), 4),
                                  _segments(br, bc, q_step, ref.step)))
    return results, round(float(covered_any[live].mean()), 4)
