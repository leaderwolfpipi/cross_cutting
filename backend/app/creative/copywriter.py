"""Copywriting: on-screen hooks / captions / labels and the post title, description and hashtags.

TemplateCopywriter works offline from a curated library and avoids hooks recently used across platforms.
LLMCopywriter (COPYWRITER=llm) asks the text model (DeepSeek) for fresh copy as JSON and falls back to
the template library on any failure. Everything passes the 广告法 lint before use.
"""

from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass, field

from .. import llm
from ..config import PLATFORM_SPECS, get_settings
from . import lint

log = logging.getLogger(__name__)


@dataclass
class ProductInfo:
    name: str = "海鲜"
    items: list[str] = field(default_factory=list)  # products that appear, e.g. 东星斑 / 红帝王蟹 / 小青龙
    selling_points: list[str] = field(default_factory=list)
    origin: str = ""
    price: str = ""
    store: str = ""
    city: str = ""
    cta: str = ""
    keywords: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict | None) -> ProductInfo:
        d = d or {}
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d and d[k] is not None})

    def item(self, rng: random.Random, prefer: str = "") -> str:
        if prefer:
            return prefer
        return rng.choice(self.items) if self.items else self.name


@dataclass
class CopyRequest:
    product: ProductInfo
    template_id: str
    platform: str
    slots: list[dict]  # [{"caption": key|None, "label": key|None, "item": product shown}]
    avoid_hooks: set[str] = field(default_factory=set)  # hook texts / keys used recently
    seed: int = 0
    recent: list[str] = field(default_factory=list)  # newest first: hooks, titles, captions of recent videos


@dataclass
class CopyResult:
    hook: str
    hook_key: str
    captions: list[str | None]  # one per slot
    labels: list[str | None]
    title: str
    description: str
    hashtags: list[str]
    source: str = "template"
    warnings: list[str] = field(default_factory=list)
    angle: str = ""

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("hook", "hook_key", "captions", "labels", "title", "description",
                                              "hashtags", "source", "warnings", "angle")}


# ====================================================================== template library
HOOKS: dict[str, list[str]] = {
    "hook_reveal": ["这一镜的{item}\n细节有点意思", "同样是{item}\n你会先看哪里？", "镜头拉近{item}\n看见了什么？",
                    "挑{item}时\n别漏掉这一步", "这池{item}\n你先注意哪里？", "别急着选{item}\n先看完这个镜头",
                    "{item}的外观\n先看清再下单", "把{item}放大看\n细节藏不住"],
    "beat_montage": ["一段快剪\n看遍海鲜档口", "{item}的细节\n跟着节奏看", "海鲜档口\n镜头切换中", "{item}出场\n先看这一镜",
                     "从近景到全景\n看清这池{item}", "镜头快一点\n细节别错过"],
    "arrival_story": ["跟着镜头\n走进海鲜档口", "从水池到细节\n看这池{item}", "档口里的一分钟\n先从{item}看起",
                      "这一池{item}\n镜头慢慢看", "挑海鲜之前\n先逛一圈"],
    "buying_tips": ["{item}怎么挑？\n记住这3招", "挑{item}别只看大小\n这几点更重要", "买{item}不踩坑\n老板教的3个方法",
                    "新手买{item}\n先看这条", "{item}好不好\n一看二摸三掂量"],
    "deal_promo": ["今天的{item}\n福利价来了", "{price}的{item}\n值不值？", "这批{item}\n今天到店就有优惠",
                   "想吃{item}的\n今天别错过", "{item}到货\n限量供应"],
    "asmr_immersive": ["放慢一点\n看{item}的纹理", "不赶时间\n细看这池{item}", "把镜头留给{item}",
                       "海鲜档口的\n慢镜头", "靠近一点\n看清{item}", "留十秒\n看{item}的细节"],
}
SELLING = ["{item} · 镜头里的细节", "从外观到动作 · 慢慢看", "看看外观 · 再看看状态", "近景观察 · 细节更清楚",
           "买前多看几眼 · 少一点犹豫", "{item} · 先看清再挑选", "水池实拍 · 看看这批{item}"]
BADGES = ["细节特写", "档口实拍", "近景观察", "挑选记录", "水池实拍", "镜头笔记"]
STORY_OPEN = ["镜头先看水池", "跟着镜头走进档口", "从环境到特写", "先看这一池"]
TIPS = {
    "tip1": ["第1招 看活力\n反应快的才新鲜", "第1招 看外观\n颜色鲜亮有光泽"],
    "tip2": ["第2招 看细节\n壳鳞完整不发黑", "第2招 闻一闻\n只有海水的清香"],
    "tip3": ["第3招 掂一掂\n越压手肉越满", "第3招 看动作\n拿起来劲儿足"],
    "tips_summary": ["记住这3招\n买海鲜不踩坑", "收藏起来\n下次照着挑"],
}
PRICE = ["今日价 {price}", "今天到店价 {price}", "到港价 {price} · 先到先得"]
CTA = ["关注我\n继续看海鲜档口实拍", "想吃的扣1\n带你去{store}", "收藏起来\n下次买海鲜不踩坑",
       "{store}见\n一起看看更多细节", "点个关注\n接着看更多海鲜镜头"]
SOFT_CTA = ["这一口鲜\n值得等", "喜欢就点个关注吧", "下次见\n带你看更多鲜货"]
TITLES = {
    "douyin": ["{hook1}", "{item}这样看更清楚", "{item}这么挑才不踩坑", "{store}实拍：{item}"],
    "kuaishou": ["{hook1}", "老铁们看看这池{item}", "{item}近景看细节", "这池{item}你怎么看"],
    "channels": ["{item}档口实拍", "{item}挑选指南", "海鲜档口日记", "{item}细节观察"],
    "xiaohongshu": ["{item}怎么挑｜3招不踩坑", "{item}近景观察", "海鲜档口探店｜{item}", "{city}吃海鲜看这篇"],
}
DESCRIPTIONS = ["{sp}。{store_line}", "镜头里是{items}，{sp}。{store_line}", "{sp}，喜欢的话点个关注。{store_line}"]


def _bigrams(text: str) -> set[str]:
    t = re.sub(r"[\s\W_]+", "", text or "")
    return {t[i:i + 2] for i in range(len(t) - 1)} or ({t} if t else set())


def similarity(a: str, b: str) -> float:
    """Character-bigram Jaccard: robust for short Chinese copy where word segmentation is unreliable."""
    x, y = _bigrams(a), _bigrams(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def staleness(text: str, recent: list[str]) -> float:
    return max((similarity(text, r) for r in recent), default=0.0)


def _two_lines(text: str, per_line: int = 9) -> str:
    """A question from the shot notes as a ≤16-character, ≤2-line hook, or '' when it is too long."""
    t = re.sub(r"\s+", "", text or "").rstrip("。")
    if not t or len(t) > 16:
        return ""
    if len(t) <= per_line:
        return t
    cut = next((i + 1 for i in range(len(t) // 2 - 2, len(t) // 2 + 3) if t[i] in "，、的是"), len(t) // 2)
    return t[:cut].rstrip("，、") + "\n" + t[cut:]


def _overlaps(text: str, shown: list[str]) -> bool:
    """True when a phrase (≥4 chars, split on separators) of `text` already appears on screen."""
    parts = [x.strip() for x in re.split(r"[·\n，,。！？ ]+", text) if len(x.strip()) >= 4]
    return any(part in s or s.replace("\n", "") in part for part in parts for s in shown if s)


def _fill(text: str, ctx: dict) -> str:
    out = text
    for k, v in ctx.items():
        out = out.replace("{" + k + "}", str(v))
    out = re.sub(r"\{[a-z0-9_]+\}", "", out)  # unfilled optional slots
    return re.sub(r"[ ·]+\n", "\n", out).strip(" ·\n")


class TemplateCopywriter:
    def compose(self, req: CopyRequest) -> CopyResult:
        rng = random.Random(req.seed * 7919 + 13)
        p = req.product
        main_item = p.item(rng, next((s.get("item") for s in req.slots if s.get("item")), ""))
        ctx = {"item": main_item, "items": "、".join(p.items) if p.items else p.name, "origin": p.origin,
               "price": p.price, "store": p.store or "我们店", "city": p.city or "海边"}
        pool = [h for h in HOOKS.get(req.template_id, HOOKS["hook_reveal"])
                if ("{price}" not in h or p.price) and ("{origin}" not in h or p.origin)
                and ("{city}" not in h or p.city) and "{hour}" not in h]
        fresh = [h for h in pool if h not in req.avoid_hooks and _fill(h, ctx) not in req.avoid_hooks]
        warnings = []
        visuals = [s.get("visual") or {} for s in req.slots]
        # With shot notes, the opening question comes from what the first shots show.
        seen_hooks = [_two_lines(q) for v in visuals[:3] for q in v.get("curiosity", [])]
        seen_hooks = [h for h in seen_hooks if h and staleness(h, req.recent[:40]) <= 0.5]
        if seen_hooks:
            hook_key = "vision"
            hook = rng.choice(seen_hooks)
        elif fresh:
            hook_key = rng.choice(fresh)
        else:
            # The library has been through a full rotation: reuse the hook that is least like recent copy.
            hook_key = min(pool, key=lambda h: (staleness(_fill(h, ctx), req.recent[:20]), rng.random()))
            warnings.append(f"{req.template_id} 的开头文案已全部用过，复用了最久未用的一条；建议开启视觉文案或扩充文案库")
        if hook_key != "vision":
            hook = _fill(hook_key, ctx)

        captions: list[str | None] = []
        labels: list = []
        used: set[str] = set()
        shown: list[str] = [hook]  # everything already on screen in this video
        user_points = [sp for sp in p.selling_points if sp]
        for slot in req.slots:
            c_ctx = {**ctx, "item": slot.get("item") or main_item}
            key = slot.get("caption")
            text = None
            v = slot.get("visual") or {}
            seen_here = [x for x in (v.get("highlight"), *v.get("actions", []), *v.get("details", []))
                         if x and len(x) <= 14 and x not in used and not _overlaps(x, shown)]
            if key in ("selling_point", "story_open") and seen_here:
                text = seen_here[0]  # what this very shot shows beats any library line
            elif key == "selling_point":
                choices = [x for x in user_points + SELLING if x not in used
                           and ("{origin}" not in x or p.origin) and not _overlaps(_fill(x, c_ctx), shown)]
                pattern = rng.choice(choices or SELLING)
                used.add(pattern)  # de-duplicate by pattern, not by filled text ("X · 活力满满" twice)
                text = _fill(pattern, c_ctx)
            elif key in TIPS:
                text = _fill(rng.choice(TIPS[key]), c_ctx)
            elif key == "story_open":
                text = _fill(rng.choice([s for s in STORY_OPEN if "{hour}" not in s]), c_ctx)
            elif key == "price":
                text = _fill(rng.choice(PRICE), c_ctx) if p.price else None
            elif key == "cta":
                text = _fill(p.cta or rng.choice([c for c in CTA if "{store}" not in c or p.store]), c_ctx)
            elif key == "soft_cta":
                text = _fill(rng.choice(SOFT_CTA), c_ctx)
            if text:
                used.add(text)
                shown.append(text)
            captions.append(text)
            labels.append(slot.get("label"))
        # Badges last, so they never repeat a phrase already on screen. A generic sticker over footage that has
        # been looked at reads as filler, so with shot notes a badge is shown only when it can be grounded.
        grounded = any(visuals)
        for i, want in enumerate(labels):
            if want:
                short = [x for x in visuals[i].get("details", []) + visuals[i].get("actions", [])
                         if len(x) <= 5 and not _overlaps(x, shown)]
                fresh_badges = [b for b in BADGES if not _overlaps(b, shown)] or BADGES
                labels[i] = short[0] if short else None if grounded else rng.choice(fresh_badges)
                if labels[i]:
                    shown.append(labels[i])

        title_pool = TITLES.get(req.platform, TITLES["douyin"])
        title = _fill(rng.choice(title_pool), {**ctx, "hook1": hook.replace("\n", "，")})
        sp = "，".join(rng.sample(user_points, min(2, len(user_points)))) if user_points else _fill(
            rng.choice(SELLING), ctx).replace(" · ", "，")
        store_line = f"地址：{p.store}" if p.store else ""
        description = _fill(rng.choice(DESCRIPTIONS), {**ctx, "sp": sp, "store_line": store_line})
        hashtags = self.hashtags(p, req.platform, rng)
        return finalize(CopyResult(hook, hook_key, captions, labels, title, description, hashtags,
                                   warnings=warnings), req.platform)

    @staticmethod
    def hashtags(p: ProductInfo, platform: str, rng: random.Random) -> list[str]:
        base = ["海鲜", *p.items[:2], "海鲜市场", "美食", *(p.keywords or [])]
        if p.city:
            base.append(f"{p.city}美食")
        extra = {"xiaohongshu": ["海鲜探店", "吃货日常"], "douyin": ["抖音美食推荐官"], "kuaishou": ["快手美食"],
                 "channels": ["视频号美食"]}.get(platform, [])
        tags, seen = [], set()
        for t in [*base, *rng.sample(extra, len(extra))]:
            t = t.strip("# ")
            if t and t not in seen:
                seen.add(t)
                tags.append(t)
        return tags[: PLATFORM_SPECS.get(platform, {}).get("tags_max", 5)]


def finalize(r: CopyResult, platform: str) -> CopyResult:
    """Enforce platform limits and the ad-law lint on every text field."""
    spec = PLATFORM_SPECS.get(platform, {})
    for name in ("hook", "title", "description"):
        val = getattr(r, name)
        hits = lint.lint(val)
        if hits:
            r.warnings.append(f"{name}: 已移除 {', '.join(h['match'] for h in hits)}")
            setattr(r, name, lint.clean(val))
    r.captions = [lint.clean(c) if c else c for c in r.captions]
    r.labels = [lint.clean(c) if c else c for c in r.labels]
    title_max = spec.get("title_max", 55)
    if len(r.title) > title_max:
        r.title = r.title[:title_max].rstrip("，,。 ")
    return r


# ====================================================================== LLM (DeepSeek), grounded in shot notes
# Claims a picture cannot prove. Allowed only when the operator wrote them in the product info.
CLAIMS = re.compile(r"到港|现捞|空运|直采|直发|野生|进口|产地|当天|今日|今天|刚到|凌晨|新鲜|鲜甜|好吃|美味|口感|肉质|"
                    r"饱满|肥美|品质|便宜|划算|优惠|福利|特价|限量")
PRICE_TEXT = re.compile(r"[¥￥]\s*\d|\d+(?:\.\d+)?\s*(?:元|块|斤)|[一二三四五六七八九十百千两]+\s*(?:块|元)")
# Rotated per video (persona × angle) so that two videos on the same footage still sound like different people.
ANGLES = (
    "细节放大：把注意力引到画面里一个具体部位或动作",
    "提问悬念：用画面里的现象抛一个问题，后面的镜头给出答案",
    "反差意外：用画面里出人意料的一点开场",
    "挑选眼光：从画面里看得见的地方讲怎么看、怎么挑",
    "数一数/比一比：引导观众数、比、找画面里的东西",
    "节奏短句：动词开头的短句，跟着镜头切换往前走",
)
PERSONAS = (
    "在档口干了十几年的老板，说话直、带点得意",
    "爱吃海鲜的朋友，第一次凑这么近看，忍不住惊叹",
    "喜欢讲冷知识的海鲜控，一句话点出门道",
    "逛市场的探店博主，像在给粉丝现场解说",
    "安静的旁白，只用很少的字点出画面",
    "爱玩梗的年轻人，比喻生动但不油腻",
)
# Sticker phrases every seafood account uses. They say nothing about this video, so they are rejected.
CLICHES = re.compile(r"现捞现做|今日到港|当日鲜|鲜活直发|现场加工|精挑细选|活蹦乱跳|不容错过|错过等一年|赶紧|速来|快来|"
                     r"走过路过|家人们|宝子们|老铁们|绝绝子|yyds|天花板|颜值|性价比|闭眼入|冲鸭|安排上")
EMPHASIS = re.compile(r"〖([^〖〗\n]{1,6})〗")
NOVELTY_MAX = 0.5  # character-bigram similarity above this to any recent text counts as a repeat

_SCHEMA = {
    "type": "object",
    "properties": {
        "hooks": {"type": "array", "items": {"type": "string"}, "description": "3 条候选钩子，彼此角度不同"},
        "captions": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": ["string", "null"]}, "facts": {"type": "array", "items": {"type": "string"}}}}},
        "labels": {"type": "array", "items": {"type": ["object", "null"], "properties": {
            "text": {"type": "string"}, "facts": {"type": "array", "items": {"type": "string"}}}}},
        "titles": {"type": "array", "items": {"type": "string"}, "description": "2 条候选标题"},
        "description": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["hooks", "captions", "labels", "titles", "description", "hashtags"],
}

_SYSTEM = """你是海鲜档口的短视频文案编辑。视频已经剪好，给你的是每个镜头的“画面事实”（视觉模型逐镜头看过画面后记下的），
请为这一条视频写屏幕文字和发布文案。目标：让刷到的人停下来，并且这条和别的视频一看就不一样。

写法：
- 用给定的“人设”口吻、按给定的“切入角度”组织全片：钩子抛出，字幕跟着镜头推进，结尾自然收住。
- 文字必须贴着这条视频的画面：说出观众此刻正看到的具体东西（部位、颜色、动作、数量、环境），
  能换到任何一条海鲜视频上都成立的话就是废话，不要写。
- hooks：3 条候选，三条用不同的手法（提问 / 反差 / 数字或比喻 / 指令“看它的…”）；每条≤16个汉字，可用\n分两行；
  内容来自开头几个镜头。每条可以用〖〗标出一个最抓眼的关键词（2~4个字），屏幕上会用强调色显示，例如“看它〖腿上的刺〗”。
- captions：数组长度等于镜头数，与镜头一一对应。镜头的“字幕类型”为空时填 {"text": null, "facts": []}。
  每条≤14个汉字。selling_point / story_open 类字幕必须引用该镜头的画面事实编号（facts，如 ["F3-2"]），只能引用同一镜头的编号。
  相邻字幕换着句式写，别都用“XX，XX”。cta / soft_cta 是结尾互动，要跟这条视频的画面有关（例：“你数清有几条腿了吗？”）。
  tip 类是挑选要点，只讲画面看得见的判断方法。
- labels：与镜头对应。“需要角标”的镜头给 {"text": 2~5个字, "facts": [该镜头事实编号]}，说画面里的东西（如“看尖刺”“触须在动”）；
  其余填 null。想不出贴切的就填 null，宁缺毋滥。
- titles：2 条候选，不超过标题上限，像真人发的，不要“XX：XX”式标题党。description 2~3 句口语，具体到这条视频的画面。hashtags 不带 #。

好坏对照（只看手法，别照抄）：
- 差：“今日到港 现捞现做”“鲜活海鲜 不容错过”“家人们看这个蟹”——空洞、哪条视频都能用。
- 好：“这蟹腿上的刺\n是拿来干嘛的？”“数数这缸\n有几条〖蓝点〗东星斑”“壳上一排排尖刺\n腿却是白底紫斑”。

硬性规定：
- 只能使用画面事实和产品信息里有的内容。不写价格、重量、产地、到货时间、新鲜/口感/肉质等画面证明不了的说法（产品信息里写明的除外）。
- 不用这些套话：现捞现做、今日到港、鲜活直发、精挑细选、活蹦乱跳、不容错过、家人们、宝子们、老铁们、天花板、闭眼入。
- 遵守《广告法》：不用“最”“第一”“顶级”“100%”“绝对”等绝对化用语，不写联系方式。
- 不要与“近期文案”雷同，换说法、换切入点。
只输出一个 JSON 对象，结构符合：""" + json.dumps(_SCHEMA, ensure_ascii=False)


def _facts(slots: list[dict]) -> tuple[list[dict], dict[int, set[str]]]:
    """Number every visual fact per shot (F<shot>-<n>) so the model can cite them and we can check citations."""
    shots, ids = [], {}
    for i, slot in enumerate(slots, 1):
        v = slot.get("visual") or {}
        items = [*v.get("actions", []), *v.get("details", []), v.get("highlight"), v.get("setting"), v.get("people")]
        if v.get("subject"):
            items.append(f"{v.get('count', '')}{v['subject']}（{v.get('shot_size', '')}）")
        facts = [f"F{i}-{j} {text}" for j, text in enumerate(dict.fromkeys(t for t in items if t), 1)]
        ids[i - 1] = {f.split(" ", 1)[0] for f in facts}
        shots.append({"镜头": i, "角色": slot.get("role"), "时长秒": round(float(slot.get("seconds") or 0), 1),
                      "字幕类型": slot.get("caption"), "需要角标": bool(slot.get("label")), "标注商品": slot.get("item"),
                      "画面事实": facts, "观众可能好奇": v.get("curiosity", [])})
    return shots, ids


def plain_text(text: str) -> str:
    """Copy without the 〖emphasis〗 markup (titles, post text, length checks)."""
    return text.replace("〖", "").replace("〗", "")


def _lines_ok(text: str, per_line: int, lines: int = 2) -> bool:
    parts = text.split("\n")
    return 0 < len(parts) <= lines and all(0 < len(x.strip()) <= per_line for x in parts)


class LLMCopywriter:
    """Copy from the text model (AGENT_MODEL_*), written from the vision notes of the shots in this video.

    Generate-then-select: the model proposes several hooks and titles, every field is validated (length, ad-law,
    unprovable claims, invented prices, citation of this shot's facts) and the candidate least similar to recent
    copy wins. Anything that fails falls back field by field to the template library."""

    def __init__(self) -> None:
        self.model = llm.text_model()  # raises LLMError when not configured
        self.fallback = TemplateCopywriter()

    def compose(self, req: CopyRequest) -> CopyResult:
        base = self.fallback.compose(req)  # also provides a safe default for any field the model leaves out
        spec = PLATFORM_SPECS.get(req.platform, {})
        rng = random.Random(req.seed * 104729 + 3)
        angle, persona = rng.choice(ANGLES), rng.choice(PERSONAS)
        shots, fact_ids = _facts(req.slots)
        allowed = " ".join([req.product.name, *req.product.items, *req.product.selling_points, req.product.origin,
                            req.product.store, req.product.city, *req.product.keywords])
        recent = [t for t in [*req.recent, *req.avoid_hooks] if t and t != "llm"][:80]
        payload = {
            "平台": spec.get("name", req.platform), "叙事模板": req.template_id, "人设": persona, "切入角度": angle,
            "产品信息": {k: v for k, v in req.product.__dict__.items() if v and k != "price"},
            "镜头": shots, "标题上限": spec.get("title_max", 55), "话题上限": spec.get("tags_max", 5),
            "近期文案(不要雷同)": recent[:40],
        }
        messages = [{"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        for attempt in range(2):  # one repair round with the reasons the first answer was rejected
            try:
                text = self.model.chat(messages, json_mode=True, max_tokens=4000, temperature=0.9)
                data = json.loads(text)
                if not isinstance(data, dict):
                    raise ValueError("not a JSON object")
            except (llm.LLMError, ValueError) as e:
                log.warning("copywriter model failed, using templates: %s", e)
                return base
            result, problems = self._select(data, req, base, fact_ids, allowed, recent, spec)
            if result is not None:
                result.angle = f"{persona.split('，')[0]} × {angle.split('：')[0]}"
                return finalize(result, req.platform)
            log.info("copywriter answer rejected (%s)", "; ".join(problems))
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": "以下问题需要改正后重新输出完整 JSON：" + "；".join(problems)}]
        base.warnings.append("视觉文案两次未通过校验，已使用模板文案")
        return base

    def _bad(self, text: str, allowed: str) -> str:
        """Why a generated text cannot be used, or ''."""
        cliche = CLICHES.search(text)
        if cliche:
            return f"套话“{cliche.group(0)}”"
        if lint.lint(text):
            return "含广告法风险词"
        if PRICE_TEXT.search(text):
            return "出现价格或重量"
        claim = next((m.group(0) for m in CLAIMS.finditer(text) if m.group(0) not in allowed), "")
        return f"出现画面无法证明的说法“{claim}”" if claim else ""

    def _select(self, data: dict, req: CopyRequest, base: CopyResult, fact_ids: dict[int, set[str]], allowed: str,
                recent: list[str], spec: dict) -> tuple[CopyResult | None, list[str]]:
        problems, warnings = [], []

        def best(candidates, ok) -> tuple[str, float]:
            scored = []
            for c in candidates if isinstance(candidates, list) else []:
                c = str(c or "").replace("\\n", "\n").strip()
                if c and ok(c) and not self._bad(c, allowed):
                    scored.append((staleness(c, recent), c))
            return (min(scored)[1], min(scored)[0]) if scored else ("", 1.0)

        def hook_ok(h: str) -> bool:
            plain = plain_text(h)
            return (_lines_ok(plain, 9) and len(plain.replace("\n", "")) <= 16 and h.count("〖") == h.count("〗") <= 1
                    and len(EMPHASIS.findall(h)) == h.count("〖"))

        hook, stale = best(data.get("hooks"), hook_ok)
        if not hook:
            problems.append("hooks 全部不合格（≤16字、≤2行、最多一处〖〗强调，且不得含套话或价格/产地/新鲜等说法）")
        elif stale > NOVELTY_MAX:
            problems.append(f"钩子“{hook}”与近期文案太像，请换角度")
        title, _ = best([plain_text(t) for t in data.get("titles") or [] if isinstance(t, str)],
                        lambda t: len(t) <= spec.get("title_max", 55) and "\n" not in t)
        n = len(req.slots)
        raw = list(data.get("captions") or [])[:n]
        raw += [{"text": None}] * (n - len(raw))
        captions: list[str | None] = []
        for i, (slot, item) in enumerate(zip(req.slots, raw)):
            key = slot.get("caption")
            item = item if isinstance(item, dict) else {"text": item}
            text = plain_text(str(item.get("text") or "").replace("\\n", "\n").strip())
            if not key:
                captions.append(None)
                continue
            cited = {str(f).strip() for f in item.get("facts") or []}
            needs = key in ("selling_point", "story_open") and fact_ids.get(i)
            if not text:
                why = "为空"
            elif not _lines_ok(text, 14):
                why = "超长"
            elif needs and not (cited and cited <= fact_ids[i]):
                why = "没有引用本镜头的画面事实"
            else:
                why = self._bad(text, allowed)
            if why or any(similarity(text, c) > 0.6 for c in captions if c):
                warnings.append(f"镜头{i + 1}字幕{why or '与前文重复'}，用模板替代")
                captions.append(base.captions[i])
            else:
                captions.append(text)
        if sum(1 for w in warnings if "模板" in w) > max(1, sum(1 for s in req.slots if s.get("caption")) // 2):
            problems.append("多数字幕不合格：必须≤14字、引用本镜头画面事实编号、不写画面证明不了的说法")
        if problems:
            return None, problems
        labels = []
        values = list(data.get("labels") or [])
        for i, slot in enumerate(req.slots):
            item = values[i] if i < len(values) else None
            item = item if isinstance(item, dict) else {"text": item, "facts": []}
            v = plain_text(str(item.get("text") or "").strip())
            cited = {str(f).strip() for f in item.get("facts") or []}
            grounded = not fact_ids.get(i) or (cited and cited <= fact_ids[i])
            ok = v and len(v) <= 5 and grounded and not self._bad(v, allowed) and not _overlaps(v, [hook, *captions])
            labels.append(None if not slot.get("label") else v if ok else base.labels[i])
        description = plain_text(str(data.get("description") or "").strip())
        if not description or self._bad(description, allowed):
            description = base.description
        tags = [t.strip("# ") for t in data.get("hashtags") or [] if isinstance(t, str) and t.strip("# ")]
        tags = [t for t in tags if not self._bad(t, allowed)][: spec.get("tags_max", 5)] or base.hashtags
        mismatched = sorted({f"{s.get('item')}→{(s.get('visual') or {}).get('subject')}" for s in req.slots
                             if (s.get("visual") or {}).get("matches_label") is False})
        if mismatched:
            warnings.append("画面主体与素材标注可能不符：" + "、".join(mismatched))
        return CopyResult(hook=hook, hook_key="llm", captions=captions, labels=labels, title=title or base.title,
                          description=description, hashtags=tags, source="llm_vision" if any(fact_ids.values())
                          else "llm", warnings=warnings), []


def get_copywriter():
    s = get_settings()
    if s.copywriter == "llm":
        try:
            return LLMCopywriter()
        except Exception as e:  # noqa: BLE001 - missing credentials must not stop rendering
            log.warning("LLM copywriter unavailable (%s); using templates", e)
    return TemplateCopywriter()
