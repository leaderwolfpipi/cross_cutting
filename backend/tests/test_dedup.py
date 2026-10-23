"""Fingerprint invariance / discrimination and provenance overlap."""

from __future__ import annotations

import numpy as np

from app.dedup import audio_fp, matcher, phash, provenance


def _cov(q, r, tau=14):
    (res,), lib = matcher.coverage_against(q[0], q[1], [matcher.Ref("x", "r", r[0], r[1])], max_dist=tau)
    return res.coverage


def test_mirror_invariant_hash_is_identical():
    rng = np.random.default_rng(1)
    img = rng.random((5, 32, 32))
    assert np.array_equal(phash.hash_frames(img), phash.hash_frames(img[:, :, ::-1]))


def test_hamming_matrix_matches_scalar():
    a = np.array([0, 2**64 - 1, 0x0F0F], dtype=np.uint64)
    b = np.array([0, 1, 0xFFFF], dtype=np.uint64)
    m = phash.hamming_matrix(a, b)
    for i in range(3):
        for j in range(3):
            assert m[i, j] == phash.hamming(int(a[i]), int(b[j]))


def test_transformed_copies_are_detected(clips):
    base = phash.video_hashes(clips["base"])
    for name in ("mirror", "grade", "crop", "speed"):
        cov = _cov(phash.video_hashes(clips[name]), base)
        assert cov >= 0.5, f"{name} reuse not detected (coverage {cov})"
    assert _cov(phash.video_hashes(clips["mirror"]), base) >= 0.95
    assert _cov(phash.video_hashes(clips["grade"]), base) >= 0.95


def test_unrelated_videos_do_not_match(clips):
    base = phash.video_hashes(clips["base"])
    for name in ("other1", "other2", "other3"):
        assert _cov(phash.video_hashes(clips[name]), base) <= 0.05, name
        assert _cov(base, phash.video_hashes(clips[name])) <= 0.05, name


def test_audio_fingerprint(clips):
    base = audio_fp.fingerprint_file(clips["base"])
    assert audio_fp.coverage(audio_fp.fingerprint_file(clips["grade"]), base) >= 0.8  # volume change
    assert audio_fp.coverage(audio_fp.fingerprint_file(clips["other1"]), base) <= 0.1


def test_library_coverage_counts_any_reference(clips):
    q = phash.video_hashes(clips["base"])
    refs = [matcher.Ref("x", "a", *phash.video_hashes(clips["other1"])),
            matcher.Ref("x", "b", *phash.video_hashes(clips["mirror"]))]
    pairs, lib = matcher.coverage_against(q[0], q[1], refs, max_dist=14)
    assert lib >= 0.95 and pairs[0].coverage <= 0.05 and pairs[1].segments


def _plan(*clips):
    return {"clips": [{"source_group": g, "src_in": a, "src_out": b, "speed": s, "out_duration": (b - a) / s}
                      for g, a, b, s in clips]}


def test_provenance_reuse_is_exact_and_speed_aware():
    a = _plan(("g1", 0, 4, 1.0), ("g2", 0, 2, 1.0))
    b = _plan(("g1", 2, 6, 1.0))
    assert abs(provenance.reuse(a, provenance.footprint(b)) - 2 / 6) < 1e-6
    slow = _plan(("g1", 0, 2, 0.5))  # 2 s of source shown over 4 s
    assert provenance.reuse(slow, provenance.footprint(b)) == 0.0
    assert provenance.reuse(slow, provenance.footprint(a)) == 1.0


def test_usage_ledger_decays_with_age():
    from datetime import datetime, timedelta

    now = datetime(2026, 10, 1)
    led = provenance.UsageLedger(half_life_days=7, now=now)
    led.add(_plan(("g", 0, 10, 1.0)), now)
    led.add(_plan(("g", 0, 10, 1.0)), now - timedelta(days=7))
    assert abs(led.used("g", 2, 4) - 1.5) < 1e-6
    assert led.used("g", 20, 22) == 0.0
