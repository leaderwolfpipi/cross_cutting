"""Fit on-screen text to the canvas: choose the font size and line breaks so a title never wraps mid-word at the
canvas edge (the renderer would otherwise break 红帝王蟹 into 红帝王 / 蟹)."""

from __future__ import annotations

import math
import unicodedata

BREAK_AFTER = set("，。！？、；：,.!?;:）)】」』 ·…~～")
NO_LINE_START = set("，。！？、；：,.!?;:）)】」』…~～")


def char_em(ch: str) -> float:
    if ch in "〖〗":  # emphasis markup, rendered as a colour change
        return 0.0
    if ch == " ":
        return 0.3
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 1.0
    return 0.58


def width_em(text: str, letter_spacing: float = 0.0) -> float:
    return sum(char_em(c) for c in text) + letter_spacing * max(0, len(text) - 1)


def _break_line(seg: str, max_em: float, letter_spacing: float) -> list[str]:
    """Greedy wrap that prefers breaking after punctuation and never starts a line with punctuation."""
    lines: list[str] = []
    rest = seg.strip()
    while rest:
        if width_em(rest, letter_spacing) <= max_em:
            lines.append(rest)
            break
        # longest prefix that fits
        cut, acc = 0, 0.0
        for i, ch in enumerate(rest):
            acc += char_em(ch) + (letter_spacing if i else 0)
            if acc > max_em:
                break
            cut = i + 1
        cut = max(1, cut)
        # prefer a punctuation break in the last 40% of the line
        for j in range(cut, max(1, int(cut * 0.6)) - 1, -1):
            if rest[j - 1] in BREAK_AFTER:
                cut = j
                break
        while cut < len(rest) and rest[cut] in NO_LINE_START:
            cut += 1
        lines.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    return lines


def balance(seg: str, n_lines: int, max_em: float, letter_spacing: float) -> list[str] | None:
    """Split a segment into exactly n_lines of similar width (looks better than greedy for titles)."""
    if n_lines <= 1:
        return [seg] if width_em(seg, letter_spacing) <= max_em else None
    total = width_em(seg, letter_spacing)
    target = total / n_lines
    lines, cur, acc = [], "", 0.0
    for ch in seg:
        w = char_em(ch) + letter_spacing
        if acc + w > target * 1.12 and len(lines) < n_lines - 1 and cur and ch not in NO_LINE_START:
            lines.append(cur)
            cur, acc = "", 0.0
        cur += ch
        acc += w
    lines.append(cur)
    lines = [ln.strip() for ln in lines if ln.strip()]
    return lines if all(width_em(ln, letter_spacing) <= max_em for ln in lines) else None


def fit(text: str, canvas_w: int, canvas_h: int, size: tuple[float, float] = (0.045, 0.075), max_lines: int = 2,
        margin: float = 0.84, letter_spacing: float = 0.0, outline_em: float = 0.12) -> tuple[str, float]:
    """Return (text with '\\n' line breaks, size as a fraction of canvas height).

    Preference order: keep the author's explicit lines at the largest size that fits; otherwise break at
    ' · ' separators (the dot is dropped); otherwise balance-wrap. Shrinks toward size[0] before adding lines."""
    segs = [s.strip() for s in text.replace("\\n", "\n").split("\n") if s.strip()]
    lo, hi = size

    def max_em(s: float) -> float:
        return margin * canvas_w / (s * canvas_h) - 2 * outline_em

    def fits(lines: list[str], s: float) -> bool:
        return len(lines) <= max_lines and all(width_em(ln, letter_spacing) <= max_em(s) for ln in lines)

    candidates = [segs]
    split = [part.strip() for seg in segs for part in seg.split("·") if part.strip()]
    if split != segs:
        candidates.append(split)
    for lines in candidates:
        s = hi
        while s >= lo - 1e-9:
            if fits(lines, s):
                return "\n".join(lines), round(s, 4)
            s -= 0.002
    # nothing fits as written: wrap the separator-split text at the smallest size
    m = max_em(lo)
    lines = []
    for seg in candidates[-1]:
        need = max(1, math.ceil(width_em(seg, letter_spacing) / m))
        lines.extend(balance(seg, need, m, letter_spacing) or _break_line(seg, m, letter_spacing))
    return "\n".join(lines), round(lo, 4)  # overflow: never drop copy, an extra line beats lost words
