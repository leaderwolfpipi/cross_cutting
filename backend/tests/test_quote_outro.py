"""Today's quote slideshow appended to mixed videos."""

from __future__ import annotations

from datetime import datetime

from conftest import ffmpeg


def _entries():
    rows = [("帝王蟹类", "红帝王蟹", f"{3 + i * 0.5:.1f}-{3.4 + i * 0.5:.1f}", 180 - i * 5) for i in range(6)]
    rows += [("鱼类", "海南东星斑", "1.0-1.5", 268), ("鱼类", "冰鲜野生东星斑", "1.0-2.0", 198)]
    rows += [("小青龙类", "越南小青龙", f"{i}.5-{i + 1}.5", 120 + i) for i in range(1, 12)]
    return [{"id": f"e{i}", "category": c, "product": p, "spec": s, "price": price, "qualifier": "",
             "status": "ready"} for i, (c, p, s, price) in enumerate(rows)]


def _sheet(db, service):
    from app.models import QuoteSheet

    sheet = QuoteSheet(supplier="测试水产批发", quote_date=service.today(), unit="元/斤", source_text="outro 测试原文",
                       entries=_entries(), corrections=[], confirmed_by="tester", confirmed_at=datetime.utcnow())
    db.add(sheet)
    db.commit()
    return sheet


def test_pages_follow_the_products_on_screen_and_respect_the_cap():
    from app.models import QuoteSheet
    from app.quotes.outro import select_pages

    sheet = QuoteSheet(supplier="s", quote_date="2026-10-24", unit="元/斤", source_text="", entries=_entries())
    products, pages, matched = select_pages(sheet, ["东星斑"] * 3 + ["红帝王蟹"], 6)
    assert matched and products[:2] == ["海南东星斑", "冰鲜野生东星斑"] and "红帝王蟹" in products
    assert all(row["product"] in products for _, page in pages for row in page["rows"])
    products, pages, _ = select_pages(sheet, ["小青龙"], 2)
    assert products == ["越南小青龙"] and len(pages) == 2  # truncated, never more than the cap
    products, pages, matched = select_pages(sheet, ["鲍鱼"], 3)
    assert not matched and pages  # still ends with today's quote


def test_outro_is_appended_after_the_edit_and_gated_like_a_priced_video(settings, tmp_path):
    from app.db import SessionLocal
    from app.media import ffmpeg as F
    from app.models import Project, Variant
    from app.publish.orchestrator import preflight_target
    from app.quotes import outro, service

    body = tmp_path / "body.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30", "-f", "lavfi", "-i", "sine=f=440:r=48000",
           "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(body))
    with SessionLocal() as db:
        sheet = _sheet(db, service)
        clips = [{"product": "红帝王蟹", "out_duration": 2.0}, {"product": "东星斑", "out_duration": 2.0}]
        spec = outro.spec(db, {"quote_sheet_id": sheet.id}, clips, seed=5)
        assert spec["sheet_id"] == sheet.id and spec["matched"] and spec["pages"] >= 2
        assert spec["page_transition"] in outro.PAGE_TRANSITIONS
        plan = {"canvas": {"width": 1080, "height": 1920, "fps": 30}, "audio": {"bgm": None}, "quote_outro": spec,
                "clips": [], "texts": []}
        out = tmp_path / "final.mp4"
        cards = outro.render_cards(db, spec)
        assert len(cards) == spec["pages"]
        outro.append(body, cards, spec, plan, out)
        info = F.probe(out)
        expected = 4 + outro.duration(spec) - spec["intro_transition_s"]
        assert abs(info.duration - expected) < 0.25 and info.has_audio and (info.width, info.height) == (1080, 1920)

        project = Project(name="片尾报价", product={}, material_ids=[], platforms=["douyin"], options={})
        db.add(project)
        db.commit()
        copy = {"title": "红帝王蟹实拍", "description": "镜头里的细节", "hashtags": []}
        variant = Variant(project_id=project.id, platform="douyin", status="passed", file_path="renders/outro.mp4",
                          plan=plan, copywriting=copy)
        db.add(variant)
        db.commit()
        assert service.carries_quote(variant.plan) and not service.validate_variant(db, variant)
        checks = preflight_target(db, variant, None, copy, {})
        assert next(c for c in checks if c["code"] == "quote_snapshot")["ok"]
        sheet.entries = [{**e, "price": e["price"] + 1} for e in sheet.entries]
        db.commit()
        assert "变动" in service.validate_variant(db, variant)
        assert not service.media_allowed(db, variant.file_path)
