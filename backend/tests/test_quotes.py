import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.quotes.parser import parse_sheet

SOURCE = Path(__file__).resolve().parents[1] / "app" / "quotes" / "seed_2026-10-22.txt"
HEADERS = {"X-API-Key": "test-key"}


def test_supplier_sheet_preserves_all_specs_and_corrections():
    parsed = parse_sheet(SOURCE.read_text())
    assert parsed["quote_date"] == "2026-10-22"
    assert parsed["unit"] == "元/斤"
    assert len(parsed["entries"]) == 211
    assert len({e["category"] for e in parsed["entries"]}) == 12
    corrected = next(e for e in parsed["entries"] if e["product"] == "马加丹板蟹" and e["spec"] == "1.6-1.9")
    assert corrected["price"] == 107
    assert parsed["corrections"][0]["old_price"] == 105
    assert parsed["corrections"][1]["matched"] is False
    assert (parsed["corrections"][1]["qualifier"], parsed["corrections"][1]["old_price"],
            parsed["corrections"][1]["new_price"]) == ("双须", 305, 307)
    assert all(e["status"] == "review" for e in parsed["entries"] if e["product"] == "黄金棕帝王蟹" and e["spec"].startswith("5."))


def test_history_preview_and_export_gate(settings):
    with TestClient(app) as client:
        imported = client.post("/api/quotes/import-sample", headers=HEADERS)
        assert imported.status_code == 200, imported.text
        sheet = imported.json()
        assert sheet["count"] == 211
        assert sheet["review_count"] > 0
        preview = client.get(f"/api/quotes/{sheet['id']}/categories/帝王蟹类/preview", headers=HEADERS)
        assert preview.status_code == 200, preview.text
        assert preview.json()["pages"] > 1
        assert preview.json()["image_url"]
        confirmed = client.post(f"/api/quotes/{sheet['id']}/confirm", headers=HEADERS,
                                json={"operator": "tester", "note": "已逐项核对"})
        assert confirmed.status_code == 409
        exported = client.post(f"/api/quotes/{sheet['id']}/categories/帝王蟹类/export", headers=HEADERS)
        assert exported.status_code == 409


def test_correction_lines_update_matching_rows_from_the_quote_text():
    source = """供应商
2026年10月22日
单位：元/斤
一、虾类
【越南小青龙】
1.3-1.4=145、1.3-1.6=145、1.5-1.6=145
【更正】上条越南小青龙
1.3-1.4=147、1.3-1.6=147、1.5-1.6=147（原写145有误）
其余价格无误。
【北部纽龙/澳龙】
2.3-2.4=297(双须305)
【更正】上条北部纽龙
2.3-2.4=297(双须307)（原写双须305有误）
其余价格无误。
"""
    parsed = parse_sheet(source)
    assert [e["price"] for e in parsed["entries"] if e["product"] == "越南小青龙"] == [147, 147, 147]
    lobster = next(e for e in parsed["entries"] if e["product"] == "北部纽龙/澳龙")
    assert lobster["price"] == 297
    assert lobster["qualifier"] == "(双须307)"
    qualifier_fix = next(c for c in parsed["corrections"] if c["qualifier"] == "双须")
    assert (qualifier_fix["matched"], qualifier_fix["old_price"], qualifier_fix["new_price"]) == (True, 305, 307)


def test_latest_quote_comes_from_config_file_or_url(tmp_path, monkeypatch):
    from email.message import EmailMessage

    from app.quotes.source import load_quote_text

    quote = "供应商\n2026年10月23日\n单位：元/斤\n一、蟹类\n【蟹】1-2=10\n"
    quote_file = tmp_path / "today.txt"
    quote_file.write_text(quote, encoding="utf-8")
    config = tmp_path / "source.json"
    config.write_text(json.dumps({"file": "today.txt", "url": ""}), encoding="utf-8")
    assert load_quote_text(config) == quote

    config.write_text(json.dumps({"file": "today.txt", "url": "ftp://example.com/q.txt"}), encoding="utf-8")
    with pytest.raises(ValueError, match="http"):
        load_quote_text(config)

    body = quote.encode()
    headers = EmailMessage()
    headers["Content-Type"] = "text/plain; charset=utf-8"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, _limit):
            return body

        @property
        def headers(self):
            return headers

    monkeypatch.setattr("app.quotes.source.urllib.request.urlopen", lambda *args, **kwargs: Response())
    config.write_text(json.dumps({"file": "missing.txt", "url": "https://quotes.example/today.txt"}), encoding="utf-8")
    assert load_quote_text(config) == quote


def test_import_latest_uses_configured_text(settings, monkeypatch):
    text = "供应商\n2026年10月22日\n单位：元/斤\n一、蟹类\n【蟹】1-2=9\n"
    monkeypatch.setattr("app.api.quotes.load_quote_text", lambda: text)
    with TestClient(app) as client:
        imported = client.post("/api/quotes/import-latest", headers=HEADERS)
    assert imported.status_code == 200, imported.text
    assert imported.json()["count"] == 1
    assert imported.json()["entries"][0]["price"] == 9


def test_conflicting_prices_need_visible_qualifier(settings, monkeypatch):
    from app.api import quotes

    monkeypatch.setattr(quotes, "today", lambda: "2026-10-22")
    sample = "供应商\n2026年10月22日\n单位：元/斤\n一、蟹类\n【蟹】1.0-2.0=100、1.5-2.5=120"
    with TestClient(app) as client:
        sheet = client.post("/api/quotes", headers=HEADERS, json={"source_text": sample}).json()
        first = sheet["entries"][0]
        body = {"spec": first["spec"], "price": first["price"], "qualifier": "", "status": "ready",
                "review_note": "供应商确认这是不同批次"}
        rejected = client.patch(f"/api/quotes/{sheet['id']}/entries/{first['id']}", headers=HEADERS, json=body)
        assert rejected.status_code == 422
        body["qualifier"] = "A批次"
        accepted = client.patch(f"/api/quotes/{sheet['id']}/entries/{first['id']}", headers=HEADERS, json=body)
        assert accepted.status_code == 200


def test_mixed_video_preflight_rejects_unverified_price(settings):
    from app.db import SessionLocal
    from app.models import Variant
    from app.publish.orchestrator import preflight_target

    variant = Variant(platform="douyin", status="passed", plan={"texts": [{"text": "今日价 187元/斤"}]})
    with SessionLocal() as db:
        checks = preflight_target(db, variant, None, {"title": "红帝王蟹", "description": "商品展示"}, {})
    assert next(c for c in checks if c["code"] == "unverified_price")["ok"] is False


def test_export_revision_is_accepted_by_signed_media_gate(settings, monkeypatch):
    from datetime import datetime

    from app.api.quotes import _revision
    from app.db import SessionLocal
    from app.models import QuoteSheet
    from app.quotes import service

    monkeypatch.setattr(service, "today", lambda: "2026-10-22")
    with SessionLocal() as db:
        sheet = QuoteSheet(supplier="供应商", quote_date="2026-10-22", unit="元/斤",
                           source_text="已核对原始报价", entries=[], corrections=[],
                           confirmed_by="tester", confirmed_at=datetime.utcnow())
        db.add(sheet)
        db.commit()
        assert _revision(sheet) == service.revision(sheet)
        rel = f"quotes/{sheet.id}/{_revision(sheet)}/category_00/all_specs.mp4"
        assert service.media_allowed(db, rel)
        sheet.corrections = [{"acknowledged": True}]
        db.commit()
        assert not service.media_allowed(db, rel)


def test_mixed_quote_link_blocks_changed_or_unconfirmed_prices(settings, monkeypatch):
    from datetime import datetime

    from app.db import SessionLocal
    from app.models import Project, QuoteSheet, Variant
    from app.publish.orchestrator import preflight_target
    from app.quotes import service

    monkeypatch.setattr(service, "today", lambda: "2026-10-24")
    entry = {"id": "price1", "category": "蟹类", "product": "海蟹", "spec": "1.0-1.5",
             "price": 88, "qualifier": "", "status": "ready"}
    with SessionLocal() as db:
        sheet = QuoteSheet(supplier="供应商", quote_date="2026-10-24", unit="元/斤", source_text="已核实原文",
                           entries=[entry], corrections=[], confirmed_by="tester", confirmed_at=datetime.utcnow())
        db.add(sheet)
        project = Project(name="报价混剪测试", product={"name": "海蟹"}, material_ids=[], platforms=["douyin"], options={})
        db.add(project)
        db.commit()
        snapshot = service.mixed_quote(db, sheet.id, entry["id"])
        copy = {"title": "海蟹实拍", "description": "参考报价 88 元/斤", "hashtags": []}
        variant = Variant(project_id=project.id, platform="douyin", status="passed", file_path="renders/verified_quote.mp4",
                          plan={"quote": {**snapshot, "copy": copy}, "texts": [{"text": "参考报价 88 元/斤"}]},
                          copywriting=copy)
        db.add(variant)
        db.commit()
        checks = preflight_target(db, variant, None, copy, {})
        assert next(c for c in checks if c["code"] == "unverified_price")["ok"]
        assert next(c for c in checks if c["code"] == "quote_snapshot")["ok"]
        assert service.media_allowed(db, variant.file_path)
        sheet.entries = [{**entry, "price": 99}]
        db.commit()
        assert not service.media_allowed(db, variant.file_path)
        assert service.validate_variant(db, variant)


def test_quote_url_accepts_chinese_character_limit(monkeypatch):
    from io import BytesIO

    from app.quotes import source

    class Response(BytesIO):
        headers = type("Headers", (), {"get_content_charset": lambda self: "utf-8"})()

    body = "蟹" * source.MAX_CHARS
    monkeypatch.setattr(source.urllib.request, "urlopen", lambda request, timeout: Response(body.encode("utf-8")))
    assert source._fetch("https://example.test/quote") == body
    monkeypatch.setattr(source.urllib.request, "urlopen", lambda request, timeout: Response(b"\xff"))
    with pytest.raises(ValueError, match="编码无法解析"):
        source._fetch("https://example.test/quote")
