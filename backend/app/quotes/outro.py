"""Today's quote as a slideshow at the end of every mixed video.

The rendered edit (the "body") is left untouched by the planner and both render backends. Afterwards the confirmed
quote cards of the products seen in the video are appended: a soft transition out of the footage, one card every
few seconds with a gentle slide between cards, and the BGM carried on underneath. Dedup fingerprints the body only,
because the outro is shared by every video of the day.
"""

from __future__ import annotations

import hashlib
import random
from collections import Counter
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..media import ffmpeg as F
from ..models import QuoteSheet
from . import service
from .parser import categories
from .render import POOLED, card, paginate

# Gentle transitions only: the cards carry prices, they must stay readable while moving.
INTRO_TRANSITIONS = ("fade", "fadeblack", "smoothup", "slideup")
PAGE_TRANSITIONS = ("slideleft", "slideup", "smoothleft", "smoothup", "fade")


def today_sheet(db: Session, preferred_id: str = "") -> QuoteSheet | None:
    """The most recently confirmed sheet for today that passes every publish check."""
    sheets = list(db.scalars(select(QuoteSheet).where(QuoteSheet.quote_date == service.today())))
    sheets.sort(key=lambda s: (s.id != preferred_id, -(s.confirmed_at.timestamp() if s.confirmed_at else 0)))
    for sheet in sheets:
        names = [c["name"] for c in categories(sheet.entries)]
        if names and not service.sheet_error(sheet, names):
            return sheet
    return None


def _matches(quote_product: str, shown: str) -> bool:
    # Material labels are short ("东星斑"); quote rows are specific ("海南东星斑", "冰鲜野生东星斑").
    return bool(shown) and (shown in quote_product or quote_product in shown)


def _pages(entries: list[dict], products: list[str]) -> list[tuple[str, dict]]:
    """Cards for `products` in priority order. A row the supplier lists under two sections is shown once, and
    products with one or two rows share a card across sections instead of each leaving a card nearly empty."""
    seen, rows = set(), []
    for e in entries:
        key = (e["product"], e["spec"], e["price"], e.get("qualifier", ""))
        if e["product"] in products and key not in seen:
            seen.add(key)
            rows.append(e)
    rows.sort(key=lambda e: products.index(e["product"]))
    sizes = Counter(e["product"] for e in rows)
    rows = [{**e, "category": e["category"] if sizes[e["product"]] > 2 else POOLED} for e in rows]
    return [(name, page) for name in dict.fromkeys(e["category"] for e in rows) for page in paginate(rows, name)]


def select_pages(sheet: QuoteSheet, shown: list[str], max_pages: int) -> tuple[list[str], list[tuple[str, dict]], bool]:
    """Products to show (most screen time first), their cards, and whether they matched the footage."""
    weights = Counter(shown)
    ready = list(dict.fromkeys(e["product"] for e in sheet.entries if e["status"] == "ready"))
    ranked = sorted((p for p in ready if any(_matches(p, s) for s in weights)),
                    key=lambda p: -max(weights[s] for s in weights if _matches(p, s)))
    matched = bool(ranked)
    chosen: list[str] = []
    for product in ranked or ready:
        # The busiest product is always shown (truncated if it alone exceeds the cap); others only whole.
        if chosen and len(_pages(sheet.entries, chosen + [product])) > max_pages:
            continue
        chosen.append(product)
    return chosen, _pages(sheet.entries, chosen)[:max_pages], matched


def spec(db: Session, project_options: dict, clips: list[dict], seed: int) -> dict | None:
    """The outro for one plan, or None when no confirmed quote exists for today."""
    s = get_settings()
    sheet = today_sheet(db, (project_options or {}).get("quote_sheet_id", ""))
    if sheet is None:
        return None
    shown: list[str] = []
    for c in clips:  # weight by screen time, in 0.5 s units
        shown += [c.get("product") or ""] * max(1, round(float(c.get("out_duration", 1)) * 2))
    products, pages, matched = select_pages(sheet, [p for p in shown if p], max(1, s.quote_outro_max_pages))
    rng = random.Random(seed * 31 + 7)
    page_s = round(max(2.0, s.quote_outro_page_s + rng.uniform(-0.2, 0.2)), 2)
    return {"sheet_id": sheet.id, "revision": service.revision(sheet), "quote_date": sheet.quote_date,
            "supplier": sheet.supplier, "products": products, "matched": matched, "pages": len(pages),
            "page_s": page_s, "page_transition": rng.choice(PAGE_TRANSITIONS),
            "page_transition_s": round(rng.uniform(0.4, 0.55), 2),
            "intro_transition": rng.choice(INTRO_TRANSITIONS), "intro_transition_s": round(rng.uniform(0.5, 0.7), 2)}


def duration(outro: dict) -> float:
    n, d = outro["pages"], outro["page_transition_s"]
    return n * (outro["page_s"] + d) - (n - 1) * d


def render_cards(db: Session, outro: dict) -> list[Path]:
    sheet = db.get(QuoteSheet, outro["sheet_id"])
    if sheet is None or service.revision(sheet) != outro["revision"]:
        raise ValueError("报价单已变动，请重新生成")
    pages = _pages(sheet.entries, outro["products"])[:outro["pages"]]
    key = hashlib.sha256(repr(outro["products"]).encode()).hexdigest()[:12]
    folder = get_settings().path("quotes", sheet.id, outro["revision"], "outro", key)
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, (category, page) in enumerate(pages, 1):
        path = folder / f"page_{i:02d}_of_{len(pages):02d}.png"
        if not path.is_file():
            card(sheet, category, page, i, len(pages)).save(path, optimize=True)
        paths.append(path)
    return paths


def append(body: Path, cards: list[Path], outro: dict, plan: dict, out: Path) -> None:
    """body + soft transition + card slideshow (+ the BGM continued underneath) → out."""
    info = F.probe(body)
    w, h, fps = info.width, info.height, int((plan.get("canvas") or {}).get("fps", 30))
    page_s, dp = outro["page_s"], outro["page_transition_s"]
    di = min(outro["intro_transition_s"], info.duration * 0.2)
    shown = page_s + dp
    total = duration(outro)
    inputs = ["-i", str(body)]
    for path in cards:
        inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{shown:.3f}", "-i", str(path)]
    parts = [f"[0:v]fps={fps},scale={w}:{h},format=yuv420p,setsar=1,settb=AVTB,setpts=PTS-STARTPTS[body]"]
    for k in range(1, len(cards) + 1):
        parts.append(f"[{k}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                     f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=#F6F4ED,fps={fps},format=yuv420p,setsar=1,"
                     f"settb=AVTB,setpts=PTS-STARTPTS[p{k}]")
    slides, running = "[p1]", shown
    for k in range(2, len(cards) + 1):
        parts.append(f"{slides}[p{k}]xfade=transition={outro['page_transition']}:duration={dp:.3f}:"
                     f"offset={running - dp:.3f}[s{k}]")
        slides, running = f"[s{k}]", running + shown - dp
    parts.append(f"[body]{slides}xfade=transition={outro['intro_transition']}:duration={di:.3f}:"
                 f"offset={info.duration - di:.3f},format=yuv420p[vout]")

    audio_in = len(cards) + 1
    bgm = (plan.get("audio") or {}).get("bgm")
    if bgm and Path(bgm["path"]).is_file():
        # Pick the track up where the body's copy of it is when the transition starts, so the cross-fade joins
        # the same music to itself instead of restarting it.
        start = float(bgm.get("src_in", 0.0)) + info.duration - di
        if float(bgm.get("duration") or 0) > 1:
            start %= float(bgm["duration"])
        inputs += ["-stream_loop", "-1", "-ss", f"{start:.3f}", "-i", bgm["path"]]
        parts.append(f"[{audio_in}:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,loudnorm=I=-19:TP=-2:LRA=11,"
                     f"aresample=48000,afade=t=out:st={max(0.0, total - 1.6):.3f}:d=1.6,"
                     f"aformat=sample_fmts=fltp:channel_layouts=stereo[oa]")
    else:
        inputs += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        parts.append(f"[{audio_in}:a]aformat=sample_fmts=fltp:channel_layouts=stereo[oa]")
    if info.has_audio:
        parts.append("[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[ba]")
    else:
        inputs += ["-f", "lavfi", "-t", f"{info.duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        parts.append(f"[{audio_in + 1}:a]aformat=sample_fmts=fltp:channel_layouts=stereo[ba]")
    parts.append(f"[ba][oa]acrossfade=d={di:.3f}:c1=tri:c2=tri[aout]")
    out.parent.mkdir(parents=True, exist_ok=True)
    F.run([*F.ffmpeg_base(), *inputs, "-filter_complex", ";".join(parts), "-map", "[vout]", "-map", "[aout]",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-profile:v", "high", "-pix_fmt", "yuv420p",
           "-r", str(fps), "-g", str(fps * 2), "-color_primaries", "bt709", "-color_trc", "bt709",
           "-colorspace", "bt709", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart",
           str(out)], timeout=max(600, (info.duration + total) * 20), what="quote outro")
