"""Copywriting: on-screen hooks / captions / labels and the post title, description and hashtags.

TemplateCopywriter works offline from a curated library and never repeats a hook recently used on the same
platform. LLMCopywriter (COPYWRITER=llm) asks the text model (DeepSeek) for fresh copy as JSON and falls back to
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
    avoid_hooks: set[str] = field(default_factory=set)  # hook texts / keys used recently on this platform
    seed: int = 0


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

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("hook", "hook_key", "captions", "labels", "title", "description",
                                              "hashtags", "source", "warnings")}


# ====================================================================== template library
HOOKS: dict[str, list[str]] = {
    "hook_reveal": ["今天到港的{item}\n个头有点离谱", "这么大的{item}\n一只够几个人吃？", "{item}还在吐泡泡\n鲜到什么程度？",
                    "老板说这批{item}\n只到了这么多", "来{city}\n一定要尝的{item}", "{item}活蹦乱跳\n看完你就懂了",
                    "一口{item}\n满嘴都是海的味道", "你见过这么精神的\n{item}吗？"],
    "beat_montage": ["30秒看完\n今天到港的好货", "鲜活海鲜\n一镜到底", "今天的海鲜\n有点猛", "{item}出场\n全场安静",
                     "海鲜档口\n今日上新", "这一池子\n都是今天的鲜货"],
    "arrival_story": ["凌晨{hour}点\n{item}刚刚到港", "跟我去海鲜档口\n看看今天的货", "早上{hour}点的海鲜市场\n已经很热闹了",
                      "{origin}的{item}\n今天到了", "进货日记\n今天挑到了好{item}"],
    "buying_tips": ["{item}怎么挑？\n记住这3招", "挑{item}别只看大小\n这几点更重要", "买{item}不踩坑\n老板教的3个方法",
                    "新手买{item}\n先看这条", "{item}好不好\n一看二摸三掂量"],
    "deal_promo": ["今天的{item}\n福利价来了", "{price}的{item}\n值不值？", "这批{item}\n今天到店就有优惠",
                   "想吃{item}的\n今天别错过", "{item}到货\n限量供应"],
    "asmr_immersive": ["听，这是{item}的声音", "安静看完\n今天的鲜", "慢慢看\n{item}的鲜活", "治愈系海鲜档口",
                       "给你看看\n刚到港的{item}"],
}
SELLING = ["鲜活现捞 · 看得见的新鲜", "{item} · 当天到港不隔夜", "壳硬肉满 · 精挑细选", "支持现场加工 · 现捞现做",
           "{item} · 活力满满", "冰鲜直达 · 锁住鲜味", "肉质紧实 · 回味清甜", "{origin}直采 · 来源可查"]
BADGES = ["今日到港", "现捞现做", "鲜活直发", "产地直采", "当日鲜", "限量供应", "到店可加工"]
STORY_OPEN = ["凌晨{hour}点\n船刚靠岸", "早上{hour}点的海鲜档口", "跟着我去进货", "今天的第一批货到了"]
TIPS = {
    "tip1": ["第1招 看活力\n反应快的才新鲜", "第1招 看外观\n颜色鲜亮有光泽"],
    "tip2": ["第2招 看细节\n壳鳞完整不发黑", "第2招 闻一闻\n只有海水的清香"],
    "tip3": ["第3招 掂一掂\n越压手肉越满", "第3招 看动作\n拿起来劲儿足"],
    "tips_summary": ["记住这3招\n买海鲜不踩坑", "收藏起来\n下次照着挑"],
}
PRICE = ["今日价 {price}", "今天到店价 {price}", "到港价 {price} · 先到先得"]
PRICE_NONE = ["今日到港价 到店咨询", "数量有限 先到先得"]
CTA = ["关注我\n每天看新到港的海鲜", "想吃的扣1\n带你去{store}", "收藏起来\n下次买海鲜不踩坑",
       "{store}见\n今天的货不多", "点个关注\n明天带你看更多好货"]
SOFT_CTA = ["这一口鲜\n值得等", "喜欢就点个关注吧", "下次见\n带你看更多鲜货"]
TITLES = {
    "douyin": ["{hook1}", "今天到港的{item}，你吃过吗？", "{item}这么挑才不踩坑", "{store}今日鲜货：{item}"],
    "kuaishou": ["{hook1}", "老铁们看看今天的{item}", "{item}到货，鲜得很", "今天的{item}真不错"],
    "channels": ["今日到港{item}", "{item}挑选指南", "海鲜档口日记", "{item}鲜活到店"],
    "xiaohongshu": ["{item}怎么挑｜3招不踩坑", "今日到港的{item}🦀", "海鲜档口探店｜{item}", "{city}吃海鲜看这篇"],
}
DESCRIPTIONS = ["{sp}。{store_line}", "今天到港的{items}，{sp}。{store_line}", "{sp}，喜欢的话点个关注。{store_line}"]


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
        hour = rng.choice(["4", "5", "6"])
        ctx = {"item": main_item, "items": "、".join(p.items) if p.items else p.name, "origin": p.origin,
               "price": p.price, "store": p.store or "我们店", "city": p.city or "海边", "hour": hour}
        pool = [h for h in HOOKS.get(req.template_id, HOOKS["hook_reveal"])
                if ("{price}" not in h or p.price) and ("{origin}" not in h or p.origin)
                and ("{city}" not in h or p.city)]
        fresh = [h for h in pool if h not in req.avoid_hooks and _fill(h, ctx) not in req.avoid_hooks]
        hook_key = rng.choice(fresh or pool)
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
            if key == "selling_point":
                choices = [x for x in user_points + SELLING if x not in used and ("{origin}" not in x or p.origin)
                           and not _overlaps(_fill(x, c_ctx), shown)]
                pattern = rng.choice(choices or SELLING)
                used.add(pattern)  # de-duplicate by pattern, not by filled text ("X · 活力满满" twice)
                text = _fill(pattern, c_ctx)
            elif key in TIPS:
                text = _fill(rng.choice(TIPS[key]), c_ctx)
            elif key == "story_open":
                text = _fill(rng.choice(STORY_OPEN), c_ctx)
            elif key == "price":
                text = _fill(rng.choice(PRICE if p.price else PRICE_NONE), c_ctx)
            elif key == "cta":
                text = _fill(p.cta or rng.choice([c for c in CTA if "{store}" not in c or p.store]), c_ctx)
            elif key == "soft_cta":
                text = _fill(rng.choice(SOFT_CTA), c_ctx)
            if text:
                used.add(text)
                shown.append(text)
            captions.append(text)
            labels.append(slot.get("label"))
        # badges last, so they never repeat a phrase already on screen ("现捞现做" badge over a 现捞现做 caption)
        for i, want in enumerate(labels):
            if want:
                fresh_badges = [b for b in BADGES if not _overlaps(b, shown)] or BADGES
                labels[i] = rng.choice(fresh_badges)
                shown.append(labels[i])

        title_pool = TITLES.get(req.platform, TITLES["douyin"])
        title = _fill(rng.choice(title_pool), {**ctx, "hook1": hook.replace("\n", "，")})
        sp = "，".join(rng.sample(user_points, min(2, len(user_points)))) if user_points else _fill(
            rng.choice(SELLING), ctx).replace(" · ", "，")
        store_line = f"地址：{p.store}" if p.store else ""
        description = _fill(rng.choice(DESCRIPTIONS), {**ctx, "sp": sp, "store_line": store_line})
        hashtags = self.hashtags(p, req.platform, rng)
        return finalize(CopyResult(hook, hook_key, captions, labels, title, description, hashtags), req.platform)

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


# ====================================================================== LLM (DeepSeek)
_SCHEMA = {
    "type": "object",
    "properties": {
        "hook": {"type": "string", "description": "开头3秒的大字钩子，≤16个汉字，可用\\n分两行"},
        "captions": {"type": "array", "items": {"type": ["string", "null"]}},
        "labels": {"type": "array", "items": {"type": ["string", "null"]}},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["hook", "captions", "labels", "title", "description", "hashtags"],
    "additionalProperties": False,
}

_SYSTEM = """你是海鲜门店的短视频文案编辑，为一条已经剪好的竖屏短视频写屏幕文字和发布文案。
要求：
- 钩子（hook）在开头3秒以大字出现，要制造好奇或反差，≤16个汉字，可用\\n分两行。
- captions/labels 与给出的镜头槽位一一对应，数组长度必须等于槽位数；槽位不需要文字时填 null。字幕每条≤14个汉字，角标≤5个汉字。
- 只根据给出的产品信息写，不编造价格、产地、规格。
- 遵守《广告法》：不用“最”“第一”“顶级”“国家级”“100%”“绝对”等绝对化用语，不写联系方式。
- 标题长度不超过给定上限；话题不带#号，不超过给定数量。
- 不要使用列出的近期钩子，换一个角度。
只输出一个 JSON 对象，符合下面的 JSON Schema，不要输出其他内容：
""" + json.dumps(_SCHEMA, ensure_ascii=False)


class LLMCopywriter:
    """Copy from the text model (AGENT_MODEL_*, DeepSeek) in JSON mode."""

    def __init__(self) -> None:
        self.model = llm.text_model()  # raises LLMError when not configured
        self.fallback = TemplateCopywriter()

    def compose(self, req: CopyRequest) -> CopyResult:
        base = self.fallback.compose(req)  # also provides a safe default for any field the model leaves out
        spec = PLATFORM_SPECS.get(req.platform, {})
        payload = {
            "平台": spec.get("name", req.platform), "叙事模板": req.template_id,
            "产品": {k: v for k, v in req.product.__dict__.items() if v},
            "镜头槽位": [{"字幕类型": s.get("caption"), "需要角标": bool(s.get("label")), "画面产品": s.get("item")}
                     for s in req.slots],
            "标题上限": spec.get("title_max", 55), "话题上限": spec.get("tags_max", 5),
            "近期钩子(不要重复)": sorted(req.avoid_hooks)[:30],
        }
        try:
            text = self.model.chat([{"role": "system", "content": _SYSTEM},
                                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                                   json_mode=True, max_tokens=8000)
        except llm.LLMError as e:
            log.warning("copywriter model failed, using templates: %s", e)
            return base
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
        except ValueError as e:
            log.warning("copywriter returned no JSON, using templates: %s", e)
            return base
        n = len(req.slots)

        def fit_list(values, default):
            values = list(values or [])[:n]
            return [v or None for v in values] + default[len(values):]

        r = CopyResult(hook=(data.get("hook") or base.hook).replace("\\n", "\n"), hook_key="llm",
                       captions=fit_list(data.get("captions"), base.captions),
                       labels=fit_list(data.get("labels"), base.labels),
                       title=data.get("title") or base.title, description=data.get("description") or base.description,
                       hashtags=[t.strip("# ") for t in data.get("hashtags") or base.hashtags if t.strip("# ")],
                       source="llm")
        # keep slot semantics: no caption where the template wants none
        r.captions = [c if s.get("caption") else None for c, s in zip(r.captions, req.slots)]
        r.labels = [c if s.get("label") else None for c, s in zip(r.labels, req.slots)]
        r.hashtags = r.hashtags[: spec.get("tags_max", 5)]
        return finalize(r, req.platform)


def get_copywriter():
    s = get_settings()
    if s.copywriter == "llm":
        try:
            return LLMCopywriter()
        except Exception as e:  # noqa: BLE001 - missing credentials must not stop rendering
            log.warning("LLM copywriter unavailable (%s); using templates", e)
    return TemplateCopywriter()
