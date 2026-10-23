"""Copy compliance lint: 广告法 absolute / superlative claims and contact details that platforms penalise."""

from __future__ import annotations

import re

# 《广告法》第九条 extreme words, plus common platform-flagged claims. Patterns are specific enough not to flag
# ordinary words such as 最近 / 第一次.
_RULES: list[tuple[str, str]] = [
    (r"最(?:好|佳|大|低|便宜|新鲜|鲜|高|优|强|棒|正宗|划算|实惠|全|多|靠谱|好吃|值)", "绝对化用语（最X）"),
    (r"第一(?:名|品牌|家)|全网第一|销量第一|排名第一|NO\.?1|No\.?1|TOP\s*1", "绝对化用语（第一）"),
    (r"唯一|独一无二|独家|首个|首家|首选|史上|全网最|全国最|世界级|国家级|顶级|极品|极致|王牌|冠军", "绝对化用语"),
    (r"100%|百分之百|绝对|万能|永久|无敌|零风险|包治|根治", "绝对化/保证性用语"),
    (r"(?:加|添加)?(?:微信|VX|vx|V信|威信)[:：]?\s*[A-Za-z0-9_-]{5,}|(?<!\d)1[3-9]\d{9}(?!\d)", "联系方式（平台会限流）"),
]
_COMPILED = [(re.compile(p), why) for p, why in _RULES]


def lint(text: str) -> list[dict]:
    hits = []
    for rx, why in _COMPILED:
        for m in rx.finditer(text or ""):
            hits.append({"match": m.group(0), "reason": why, "pos": m.start()})
    return hits


def clean(text: str) -> str:
    """Best-effort softening for generated copy (the generator avoids these words; this is a safety net)."""
    out = text
    for rx, _ in _COMPILED:
        out = rx.sub("", out)
    return re.sub(r"\s{2,}", " ", out).strip()
