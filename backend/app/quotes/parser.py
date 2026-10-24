"""Parse supplier messages conservatively. Never infer missing weights, units or prices."""

from __future__ import annotations

import re
import uuid
from datetime import date

SECTION = re.compile(r"^[一二三四五六七八九十]+、(.+)$")
PRODUCT = re.compile(r"^【([^】]+)】(.*)$")
PRICE = re.compile(r"^(.+?)\s*=\s*(\d+(?:\.\d+)?)(.*)$")
DATE = re.compile(r"(20\d{2})年(\d{1,2})月(\d{1,2})日")
def _id() -> str:
    return uuid.uuid4().hex[:12]


def _qualifier_amount(text: str, name: str) -> int | None:
    if not name:
        return None
    found = re.search(re.escape(name) + r"\s*(\d+)", text)
    return int(found.group(1)) if found else None


def _product_rows(entries: list[dict], name: str) -> list[dict]:
    """Match a correction title to one product. A shorter title may name the part before '/'."""
    exact = [e for e in entries if e["product"] == name]
    if exact:
        return exact
    matched: list[str] = []
    for product in dict.fromkeys(e["product"] for e in entries):
        aliases = [product, *[part for part in product.split("/") if part]]
        if any(alias == name or alias.startswith(name) or name.startswith(alias) for alias in aliases):
            matched.append(product)
    if len(matched) == 1:
        return [e for e in entries if e["product"] == matched[0]]
    return []


def _correction_target(entries: list[dict], product: str, spec: str, qualifier: str, old_price: int | None) -> dict | None:
    hits = [e for e in _product_rows(entries, product) if e["spec"] == spec]
    if qualifier and old_price is not None:
        named = [e for e in hits if str(old_price) in e.get("qualifier", "")]
        if len(named) == 1:
            return named[0]
    return hits[0] if len(hits) == 1 else None


def _record_corrections(line: str, product: str, entries: list[dict], corrections: list[dict], lineno: int) -> None:
    """Apply every spec on a supplier correction line. Prices come only from that line."""
    mention = re.search(r"原写([^\d）]*?)(\d+)有误", line)
    qualifier = mention.group(1).strip() if mention else ""
    mentioned_old = int(mention.group(2)) if mention else None
    note = re.search(r"（原写.*?有误）", line)
    body = (line[:note.start()] + line[note.end():]) if note else line
    for token in [part.strip() for part in body.split("、") if "=" in part]:
        found = PRICE.match(token)
        if not found:
            continue
        spec, raw_price, _suffix = found.groups()
        spec, main_price = spec.strip(), int(raw_price) if raw_price.isdigit() else float(raw_price)
        new_qualifier_price = _qualifier_amount(token, qualifier) or _qualifier_amount(line, qualifier)
        target = _correction_target(entries, product, spec, qualifier, mentioned_old)
        applied = False
        recorded_old = mentioned_old
        if target and qualifier and new_qualifier_price is not None and mentioned_old is not None and str(mentioned_old) in target.get("qualifier", ""):
            target["qualifier"] = target["qualifier"].replace(str(mentioned_old), str(new_qualifier_price), 1)
            target["review_note"] = f"原价{qualifier} {mentioned_old}，按第 {lineno} 行更正为 {new_qualifier_price}"
            applied = True
        elif target and not qualifier:
            recorded_old = target["price"]
            target["price"] = main_price
            target["review_note"] = f"原价 {recorded_old}，按第 {lineno} 行更正为 {main_price}"
            applied = True
        new_price = new_qualifier_price if qualifier and new_qualifier_price is not None else main_price
        corrections.append({"id": _id(), "product": product, "spec": spec, "new_price": new_price,
                            "old_price": recorded_old,
                            "qualifier": qualifier, "main_price": main_price, "source_line": lineno,
                            "matched": applied, "acknowledged": applied, "review_note": "", "raw": token.strip()})


def _range(spec: str) -> tuple[float, float] | None:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", spec)
    return (float(match.group(1)), float(match.group(2))) if match else None


def parse_sheet(source: str) -> dict:
    lines = [line.strip() for line in source.splitlines() if line.strip()]
    if not lines or not DATE.search(source):
        raise ValueError("报价原文需包含供应商名称和 YYYY年M月D日 日期")
    match = DATE.search(source)
    assert match
    quote_date = date(*map(int, match.groups())).isoformat()
    supplier = lines[0]
    unit = re.search(r"单位[：:]\s*([^\s]+)", source)
    if not unit:
        raise ValueError("报价原文需注明计价单位")
    entries: list[dict] = []
    corrections: list[dict] = []
    category = product = ""
    in_correction = False
    pending_correction = ""
    for lineno, line in enumerate(lines, 1):
        section = SECTION.match(line)
        if section:
            category, product, in_correction = section.group(1), "", False
            continue
        header = PRODUCT.match(line)
        if header:
            name, body = header.groups()
            if name == "更正":
                in_correction = True
                pending_correction = body.replace("上条", "").strip()
                continue
            category = category or "未分类"
            product, in_correction = name, False
            line = body.strip()
            if not line:
                continue
            if line.startswith("="):
                line = "未标规格" + line
        if in_correction:
            if line.startswith("其余") or line.startswith("原写") or "=" not in line:
                in_correction = False
                continue
            _record_corrections(line, pending_correction, entries, corrections, lineno)
            continue
        if not product or "=" not in line:
            continue
        for token in line.split("、"):
            found = PRICE.match(token.strip())
            if not found:
                continue
            spec, price, qualifier = found.groups()
            spec = spec.strip()
            qualifier = qualifier.strip()
            # Single numbers with three decimals may be a supplier's shorthand, not a precise weight.
            ambiguous = spec == "未标规格" or bool(re.fullmatch(r"\d+\.\d{3}", spec))
            entries.append({"id": _id(), "category": category, "product": product, "spec": spec,
                            "price": int(price) if price.isdigit() else float(price), "qualifier": qualifier,
                            "source_line": lineno, "raw": token.strip(), "status": "review" if ambiguous else "ready",
                            "review_note": "原文未标规格或三位小数规格需确认含义" if ambiguous else ""})
    # Identical rows may recur in multiple sections; preserve one display row and its first source line.
    seen: set[tuple] = set()
    unique = []
    for e in entries:
        key = (e["category"], e["product"], e["spec"], e["price"], e["qualifier"])
        if key not in seen:
            unique.append(e)
            seen.add(key)
    entries = unique
    for e in entries:
        peers = [p for p in entries if p is not e and p["product"] == e["product"] and
                 p["spec"] == e["spec"] and (p["price"], p["qualifier"]) != (e["price"], e["qualifier"])]
        if peers:
            e["status"] = "review"
            e["review_note"] = (e["review_note"] + "；" if e["review_note"] else "") + "同商品同规格存在不同报价"
        interval = _range(e["spec"])
        if interval and any((other := _range(p["spec"])) and max(interval[0], other[0]) < min(interval[1], other[1])
                            and p["price"] != e["price"] for p in entries if p is not e and p["product"] == e["product"]):
            e["status"] = "review"
            e["review_note"] = (e["review_note"] + "；" if e["review_note"] else "") + "重量档重叠且价格不同，需核对批次/状态"
    return {"supplier": supplier, "quote_date": quote_date, "unit": unit.group(1),
            "entries": entries, "corrections": corrections}


def categories(entries: list[dict]) -> list[dict]:
    out = []
    for name in dict.fromkeys(e["category"] for e in entries):
        rows = [e for e in entries if e["category"] == name]
        out.append({"name": name, "count": len(rows), "review_count": sum(e["status"] != "ready" for e in rows),
                    "products": list(dict.fromkeys(e["product"] for e in rows))})
    return out


def ambiguous_ready_pairs(rows: list[dict]) -> bool:
    """Overlapping prices need distinct labels that viewers can see on the card."""
    for i, left in enumerate(rows):
        for right in rows[i + 1:]:
            if left["product"] != right["product"] or left["price"] == right["price"]:
                continue
            a, b = _range(left["spec"]), _range(right["spec"])
            overlaps = left["spec"] == right["spec"] or (a and b and max(a[0], b[0]) < min(a[1], b[1]))
            if overlaps and (not left.get("qualifier") or not right.get("qualifier") or
                             left["qualifier"] == right["qualifier"]):
                return True
    return False
