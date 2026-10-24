"""L1 provenance dedup on edit decision lists: which source footage (source_group, time range) a video uses.

Exact and invariant to every visual transform (mirror, grade, crop, speed, re-encode), and cheap enough to run
before rendering. reuse(A, B) = output seconds of A whose source frames also appear in B / duration of A.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

Interval = tuple[float, float]


def merge(intervals: Iterable[Interval]) -> list[Interval]:
    out: list[list[float]] = []
    for a, b in sorted(intervals):
        if b <= a:
            continue
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def intersect_len(a: Interval, merged: list[Interval]) -> float:
    total = 0.0
    for b0, b1 in merged:
        if b1 <= a[0]:
            continue
        if b0 >= a[1]:
            break
        total += min(a[1], b1) - max(a[0], b0)
    return max(0.0, total)


def plan_clips(plan: dict) -> list[dict]:
    return [c for c in plan.get("clips", []) if c.get("source_group") and c.get("src_out", 0) > c.get("src_in", 0)]


def plan_duration(plan: dict) -> float:
    return sum(c.get("out_duration", 0.0) for c in plan.get("clips", [])) or 0.0


def footprint(plan: dict) -> dict[str, list[Interval]]:
    """source_group → merged source intervals used by the plan (including split-screen secondaries)."""
    groups: dict[str, list[Interval]] = defaultdict(list)
    for c in plan_clips(plan):
        groups[c["source_group"]].append((c["src_in"], c["src_out"]))
    return {g: merge(iv) for g, iv in groups.items()}


def opening(plan: dict, seconds: float = 3.0) -> dict:
    """The source intervals seen in the first `seconds` of an edit."""
    remaining, clips = seconds, []
    for c in plan_clips(plan):
        duration = float(c.get("out_duration") or 0)
        if duration <= 0:
            continue
        taken = min(remaining, duration)
        end = c["src_in"] + (c["src_out"] - c["src_in"]) * taken / duration
        clips.append({**c, "src_out": end, "out_duration": taken})
        remaining -= taken
        if remaining <= 1e-6:
            break
    return {"clips": clips}


def opening_reuse(plan: dict, other: dict, seconds: float = 3.0) -> float:
    return reuse(opening(plan, seconds), footprint(opening(other, seconds)))


def reuse(plan: dict, other_footprint: dict[str, list[Interval]]) -> float:
    dur = plan_duration(plan)
    if dur <= 0:
        return 0.0
    shared = 0.0
    for c in plan_clips(plan):
        merged = other_footprint.get(c["source_group"])
        if not merged:
            continue
        src_len = c["src_out"] - c["src_in"]
        if src_len <= 0:
            continue
        shared += intersect_len((c["src_in"], c["src_out"]), merged) / src_len * c.get("out_duration", src_len)
    return min(1.0, shared / dur)


def union_footprint(footprints: Iterable[dict[str, list[Interval]]]) -> dict[str, list[Interval]]:
    acc: dict[str, list[Interval]] = defaultdict(list)
    for fp in footprints:
        for g, iv in fp.items():
            acc[g].extend(iv)
    return {g: merge(iv) for g, iv in acc.items()}


class UsageLedger:
    """How much each stretch of footage has been used recently (decayed), for freshness-first clip selection."""

    def __init__(self, half_life_days: float = 14.0, now: datetime | None = None):
        self.half_life = max(0.1, half_life_days)
        self.now = now or datetime.utcnow()
        self.usage: dict[str, list[tuple[float, float, float]]] = defaultdict(list)  # group → (a, b, weight)

    def add(self, plan: dict, when: datetime | None) -> None:
        age_days = max(0.0, ((self.now - when).total_seconds() / 86400.0) if when else 0.0)
        w = math.pow(0.5, age_days / self.half_life)
        for g, ivs in footprint(plan).items():
            for a, b in ivs:
                self.usage[g].append((a, b, w))

    def used(self, group: str, a: float, b: float) -> float:
        """Decay-weighted number of times [a, b] has been used (0 = never)."""
        if b <= a:
            return 0.0
        total = 0.0
        for u0, u1, w in self.usage.get(group, ()):
            ov = min(b, u1) - max(a, u0)
            if ov > 0:
                total += w * ov
        return total / (b - a)
