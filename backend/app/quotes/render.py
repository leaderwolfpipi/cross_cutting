"""Legible, screenshot-friendly quote cards and a video assembled from those cards."""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
INK = "#172E3B"
MUTED = "#71818A"
TEAL = "#187E81"
GOLD = "#E2AB55"
CREAM = "#F6F4ED"
POOLED = "今日报价"  # a card that gathers small products from several sections (mixed-video outro)


@lru_cache(maxsize=16)
def _font(size: int, bold: bool = False):
    family = "PingFang SC:style=Semibold" if bold else "PingFang SC"
    path = subprocess.run(["fc-match", "-f", "%{file}", family], capture_output=True, text=True,
                          timeout=5).stdout.strip()
    if not path:
        raise RuntimeError("未找到中文字体，请安装 PingFang SC 或 Noto Sans CJK SC")
    return ImageFont.truetype(path, size)


def paginate(entries: list[dict], category: str) -> list[dict]:
    rows = [e for e in entries if e["category"] == category and e["status"] != "excluded"]
    pages = []
    small: list[dict] = []

    def flush_small():
        nonlocal small
        for start in range(0, len(small), 4):
            pages.append({"product": "品类速览", "rows": small[start:start + 4], "product_page": 1, "product_pages": 1})
        small = []

    for product in dict.fromkeys(e["product"] for e in rows):
        own = [e for e in rows if e["product"] == product]
        if len(own) <= 2:
            if len(small) + len(own) > 4:
                flush_small()
            small.extend(own)
            continue
        flush_small()
        for start in range(0, len(own), 4):
            pages.append({"product": product, "rows": own[start:start + 4],
                          "product_page": start // 4 + 1, "product_pages": (len(own) + 3) // 4})
    flush_small()
    return pages


def _fit(draw: ImageDraw.ImageDraw, text: str, max_width: int, size: int, min_size: int = 26, bold=False):
    while size > min_size and draw.textbbox((0, 0), text, font=_font(size, bold))[2] > max_width:
        size -= 2
    return _font(size, bold)


def card(sheet, category: str, page: dict, page_no: int, total: int) -> Image.Image:
    image = Image.new("RGB", (W, H), CREAM)
    d = ImageDraw.Draw(image)
    d.rectangle((0, 0, W, 142), fill=INK)
    d.text((82, 48), sheet.supplier[:22], font=_fit(d, sheet.supplier[:22], 900, 40), fill="white")
    if sheet.quote_date != datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat():
        d.rounded_rectangle((778, 38, 1000, 106), radius=18, fill="#AE453D")
        d.text((800, 52), "历史报价预览", font=_font(29, True), fill="white")
    title = category if category == POOLED else category + " · 全规格报价"
    d.text((82, 226), title, font=_fit(d, title, 916, 62, 42, True), fill=INK)
    d.text((82, 326), f"{sheet.quote_date}  ·  {sheet.unit}", font=_font(35), fill=MUTED)
    d.rounded_rectangle((68, 414, 1012, 1338), radius=34, fill="white")
    d.rounded_rectangle((94, 445, 986, 559), radius=20, fill="#E9F3F0")
    d.text((126, 466), page["product"], font=_fit(d, page["product"], 680, 51, 36, True), fill=TEAL)
    d.text((842, 485), f"{page['product_page']}/{page['product_pages']}", font=_font(31), fill=MUTED)
    d.text((128, 605), "规格 / 状态", font=_font(29), fill=MUTED)
    d.text((817, 605), "报价", font=_font(29), fill=MUTED)
    positions = {1: [910], 2: [800, 1030], 3: [735, 930, 1125], 4: [675, 826, 977, 1128]}
    for i, row in enumerate(page["rows"]):
        y = positions[len(page["rows"])][i]
        if i:
            d.line((128, y - 25, 955, y - 25), fill="#E5EAE7", width=2)
        spec = row["spec"]
        if row.get("qualifier"):
            spec += " " + row["qualifier"]
        if page["product"] == "品类速览" and row["spec"] != row["product"]:
            d.text((128, y - 15), row["product"], font=_fit(d, row["product"], 600, 29, 24), fill=MUTED)
            d.text((128, y + 29), spec, font=_fit(d, spec, 600, 41, 28, True), fill=INK)
        else:
            d.text((128, y), spec, font=_fit(d, spec, 600, 43, 28, True), fill=INK)
        price = str(row["price"])
        width = d.textbbox((0, 0), price, font=_font(57, True))[2]
        d.text((936 - width, y - 7), price, font=_font(57, True), fill=TEAL)
    d.rounded_rectangle((68, 1392, 1012, 1525), radius=22, fill="#E7E5DB")
    footer = "含已更正报价 · 供应商报价参考" if any("原价" in e.get("review_note", "") for e in page["rows"]) else "供应商报价参考 · 规格按原文展示"
    d.text((102, 1424), footer, font=_fit(d, footer, 875, 34), fill=INK)
    d.line((82, 1600, 998, 1600), fill=GOLD, width=4)
    d.text((82, 1640), "暂停截图  ·  查看完整规格", font=_font(34), fill=MUTED)
    d.text((900, 1640), f"{page_no}/{total}", font=_font(34, True), fill=TEAL)
    return image


def render_preview(sheet, category: str, target: Path) -> int:
    pages = paginate(sheet.entries, category)
    if not pages:
        raise ValueError("该品类没有可展示的报价")
    target.parent.mkdir(parents=True, exist_ok=True)
    card(sheet, category, pages[0], 1, len(pages)).save(target, optimize=True)
    return len(pages)


def export_category(sheet, category: str, target_dir: Path) -> dict:
    pages = paginate(sheet.entries, category)
    if not pages:
        raise ValueError("该品类没有可展示的报价")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未安装 FFmpeg")
    target_dir.mkdir(parents=True, exist_ok=True)
    pngs = []
    for i, page in enumerate(pages, 1):
        path = target_dir / f"page_{i:03d}.png"
        frame = card(sheet, category, page, i, len(pages))
        frame.save(path, optimize=True)
        if i == 1:
            frame.save(target_dir / "cover.jpg", quality=92, optimize=True)
        pngs.append(path)
    # A contact sheet is useful for saving and checking every price at once.
    long_image = Image.new("RGB", (W, H * len(pngs)), CREAM)
    for i, path in enumerate(pngs):
        with Image.open(path) as im:
            long_image.paste(im, (0, i * H))
    long_path = target_dir / "all_specs.png"
    long_image.save(long_path, optimize=True)
    manifest = target_dir / "frames.txt"
    manifest.write_text("".join(f"file '{p.name}'\nduration 5\n" for p in pngs) + f"file '{pngs[-1].name}'\n")
    video = target_dir / "all_specs.mp4"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-safe", "0", "-f", "concat",
                    "-i", str(manifest), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                    "-vf", "fps=30", "-t", str(len(pngs) * 5),
                    "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "24", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
                    "-movflags", "+faststart", str(video)], check=True,
                   timeout=600)
    return {"video": video, "image": long_path, "cover": target_dir / "cover.jpg", "pages": len(pngs)}
