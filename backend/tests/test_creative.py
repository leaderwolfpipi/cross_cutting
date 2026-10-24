"""Copy, text fitting, lint and planning (no rendering)."""

from __future__ import annotations

import random

from app.creative import lint, textfit
from app.creative.copywriter import CopyRequest, ProductInfo, TemplateCopywriter
from app.creative.planner import MaterialView, PlanInput, _sample, plan
from app.creative.templates import TEMPLATES
from app.config import PLATFORM_SPECS
from app.dedup.provenance import UsageLedger, footprint, opening_reuse, reuse


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


def test_window_sampling_does_not_hide_other_source_groups():
    a, b = _materials(2)
    candidates = [(1.0, a, i * 0.25, i * 0.25 + 2.0) for i in range(100)]
    candidates += [(0.95, b, 0.0, 2.0)]
    selected = {_sample(candidates, random.Random(seed))[1].source_group for seed in range(30)}
    assert selected == {a.source_group, b.source_group}


def test_opening_overlap_detects_a_reused_hook_even_when_full_edits_differ():
    def clip(group, start):
        return {"source_group": group, "src_in": start, "src_out": start + 3, "out_duration": 3}

    first = {"clips": [clip("hook", 0), clip("body_a", 0)]}
    second = {"clips": [clip("hook", 0), clip("body_b", 0)]}
    assert reuse(second, footprint(first)) == 0.5
    assert opening_reuse(second, first) == 1.0


def test_style_rotation_changes_visible_choices_and_platform_canvas():
    inp = PlanInput("xiaohongshu", TEMPLATES["hook_reveal"], _materials(), ProductInfo(items=["东星斑"]),
                    31, UsageLedger())
    first = plan(inp)
    inp.avoid_styles.add(first["style"]["key"])
    second = plan(inp)
    assert first["style"]["key"] != second["style"]["key"]
    assert first["canvas"]["width"] == 1080 and first["canvas"]["height"] == 1920
    assert (PLATFORM_SPECS["xiaohongshu"]["cover_width"],
            PLATFORM_SPECS["xiaohongshu"]["cover_height"]) == (1080, 1920)
    assert first["texts"][0]["preset"] == first["style"]["hook_preset"]
    assert all(t["style"]["position_y"] <= 0.73 for t in first["texts"] if t["role"] == "caption")
    assert all(not c["transition_out"] or c["transition_out"]["kind"] in
               {"cross_dissolve", "dissolve", "dip_to_black", "cam_left", "cam_right", "cam_up",
                "cam_down", "push_left", "push_right", "zoom_in"} for c in first["clips"])


def test_verified_quote_has_readable_early_or_late_reveal():
    quote = {"product": "东星斑", "spec": "1.0-1.5", "price": 88.0, "unit": "元/斤",
             "quote_date": "2026-10-24", "supplier": "测试供应商", "placement": "early"}
    inp = PlanInput("douyin", TEMPLATES["hook_reveal"], _materials(), ProductInfo(items=["东星斑"]),
                    52, UsageLedger(), quote=quote)
    early = plan(inp)
    full = next(t for t in early["texts"] if t["role"] == "price")
    assert "88 元/斤" in full["text"] and full["duration"] >= 2.2
    assert "88.0" not in full["text"]
    assert full["start"] < early["duration"] / 2
    assert "88 元/斤" in early["copy"]["description"]
    assert early["quote"]["copy"]["description"] == early["copy"]["description"]
    assert all(t["role"] not in ("caption", "label", "cta") or
               t["start"] + t["duration"] <= full["start"] or
               t["start"] >= full["start"] + full["duration"] for t in early["texts"])
    assert len(next(t for t in early["texts"] if t["role"] == "price_source")["text"].splitlines()) <= 2
    inp.quote = {**quote, "placement": "late"}
    late = plan(inp)
    assert any(t["role"] == "price_preview" for t in late["texts"])
    assert next(t for t in late["texts"] if t["role"] == "price")["start"] > late["duration"] / 2
    assert not any(t["role"] == "cta" for t in late["texts"])


def test_long_supplier_does_not_make_a_three_line_price_footer():
    quote = {"product": "东星斑", "spec": "1.0-1.5", "price": 88, "unit": "元/斤",
             "quote_date": "2026-10-24", "supplier": "华东地区测试供应商海鲜批发有限公司今日报价中心", "placement": "early"}
    result = plan(PlanInput("douyin", TEMPLATES["hook_reveal"], _materials(),
                            ProductInfo(items=["东星斑"]), 53, UsageLedger(), quote=quote))
    footer = next(t for t in result["texts"] if t["role"] == "price_source")
    assert len(footer["text"].splitlines()) <= 2
    assert quote["supplier"] in result["copy"]["description"]


def test_batch_reserves_distinct_narratives_across_platforms(settings):
    from app.db import SessionLocal
    from app.models import Project
    from app.services.generate import new_variants

    with SessionLocal() as db:
        project = Project(name="跨平台叙事轮换", product={}, material_ids=[],
                          platforms=["douyin", "kuaishou", "channels", "xiaohongshu"], options={})
        db.add(project)
        db.flush()
        variants = new_variants(db, project, project.platforms, 2)
        assert len({v.template_id for v in variants[:4]}) == 4
        assert len({v.plan_report["reserved_style"]["key"] for v in variants}) == len(variants)
        for platform in project.platforms:
            pair = [v for v in variants if v.platform == platform]
            assert pair[0].template_id != pair[1].template_id
            styles = [v.plan_report["reserved_style"] for v in pair]
            assert styles[0]["look"] != styles[1]["look"]
            assert styles[0]["rhythm"] != styles[1]["rhythm"]
        db.rollback()
