"""Copy, text fitting, lint and planning (no rendering)."""

from __future__ import annotations

import random

from app.creative import lint, textfit
from app.creative.copywriter import CopyRequest, ProductInfo, TemplateCopywriter
from app.creative.planner import MaterialView, PlanInput, plan
from app.creative.templates import TEMPLATES
from app.dedup.provenance import UsageLedger, footprint, reuse


def test_textfit_keeps_lines_inside_canvas():
    for text in ["今天到港的红帝王蟹\n到底有多大？", "支持现场加工 · 现捞现做", "这么大的红帝王蟹一只够几个人吃还在吐泡泡"]:
        body, size = textfit.fit(text, 1080, 1920, (0.034, 0.072), 2)
        max_em = 0.84 * 1080 / (size * 1920) - 0.24
        assert all(textfit.width_em(line) <= max_em + 1e-6 for line in body.split("\n")), body
        assert not any(line.startswith("·") for line in body.split("\n"))
    assert "\n" not in textfit.fit("支持现场加工 · 现捞现做", 1080, 1920, (0.034, 0.048), 2)[0]


def test_lint_flags_ad_law_terms_but_not_ordinary_words():
    assert {h["match"] for h in lint.lint("全网最低价，第一名，100%新鲜")} >= {"最低", "第一名", "100%"}
    assert lint.lint("最近到港，第一次见这么大的蟹") == []
    assert lint.lint("加微信 abc123456 咨询") and lint.lint("电话13812345678")


def test_template_copy_avoids_recent_hooks_and_respects_limits():
    cw = TemplateCopywriter()
    prod = ProductInfo(items=["东星斑"], selling_points=["当天到港"], store="渔港码头")
    slots = [{"caption": None, "label": None, "item": "东星斑"}, {"caption": "selling_point", "label": "badge", "item": "东星斑"},
             {"caption": "cta", "label": None, "item": "东星斑"}]
    seen = set()
    for seed in range(6):
        r = cw.compose(CopyRequest(prod, "hook_reveal", "xiaohongshu", slots, avoid_hooks=seen, seed=seed))
        assert r.hook_key not in seen
        seen.add(r.hook_key)
        assert len(r.title) <= 20 and not lint.lint(r.title + r.description)
        assert r.captions[0] is None and r.captions[1] and r.labels[1]


def _materials(n=4):
    views = []
    for i in range(n):
        frames = 20
        rng = random.Random(i)
        an = {"fps": 2.0, "duration": 10.0, **{k: [rng.random() * 0.6 + 0.2 for _ in range(frames)]
                                                for k in ("motion", "sharp", "sat", "warm", "bright")},
              "info": [1] * frames}
        views.append(MaterialView(id=f"m{i}", source_group=f"g{i}", product=["东星斑", "红帝王蟹"][i % 2], width=1080,
                                  height=1920, duration=10.0, fps=60.0, analysis=an, shots=[(0.0, 10.0)], path=f"/x/{i}.mp4"))
    return views


def test_plan_structure_and_no_overlapping_windows():
    p = plan(PlanInput("douyin", TEMPLATES["hook_reveal"], _materials(), ProductInfo(items=["东星斑"]), 3, UsageLedger()))
    assert p["clips"][0]["role"] == "hook" and p["texts"][0]["role"] == "hook"
    assert 12 <= p["duration"] <= 36
    seen = {}
    for c in p["clips"]:
        for a, b in seen.get(c["source_group"], []):
            assert c["src_out"] <= a or c["src_in"] >= b, "windows overlap inside one plan"
        seen.setdefault(c["source_group"], []).append((c["src_in"], c["src_out"]))
        assert 1.0 <= c["transform"]["scale"] <= 1.6
    assert p["clips"][-1]["transition_out"] is None


def test_freshness_steers_away_from_used_footage():
    mats = _materials()
    first = plan(PlanInput("douyin", TEMPLATES["hook_reveal"], mats, ProductInfo(), 11, UsageLedger()))
    led = UsageLedger()
    from datetime import datetime

    led.add(first, datetime.utcnow())
    second = plan(PlanInput("douyin", TEMPLATES["hook_reveal"], mats, ProductInfo(), 12, led))
    naive = plan(PlanInput("douyin", TEMPLATES["hook_reveal"], mats, ProductInfo(), 12, UsageLedger()))
    assert reuse(second, footprint(first)) < reuse(naive, footprint(first)) or reuse(second, footprint(first)) < 0.35
