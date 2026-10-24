"""Shot logging with the vision model: what each shot actually shows, as short factual notes.

Runs once per shot after material analysis (a background job), never during rendering. A few frames spread over
the shot go to the vision model in one request; the answer is validated, stripped of claims a picture cannot
prove (provenance, freshness, price) and cached in ``material.analysis["vision"]`` under a prompt version, so a
prompt change re-logs automatically. The copywriter then writes each video's text from the notes of the exact
shots that video uses, which is where its variety comes from.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy.orm import Session

from .. import llm
from ..config import get_settings
from ..media import ffmpeg as F
from . import lint

log = logging.getLogger(__name__)

VERSION = "shot-v2"
# A frame cannot show where seafood came from, when it landed, what it costs or how it tastes.
UNPROVABLE = re.compile(r"到港|现捞|空运|直采|直发|野生|进口|产地|当天|今日|今天|刚到|凌晨|斤|元|价|便宜|划算|"
                        r"新鲜|鲜甜|好吃|美味|口感|肉质|饱满|肥美|顶级|品质")
# Observations that must never surface as a hook or caption, even if the model ignores the prompt.
NEGATIVE = re.compile(r"伤|死|划痕|破损|断脚|断腿|断钳|腐|烂|臭|脏|青苔|藻|翻身|翻肚|不动|奄奄")
SHOT_SIZES = ("特写", "近景", "中景", "全景")

_PROMPT = """你在给海鲜档口短视频做镜头场记。下面是同一个镜头按时间顺序抽取的 {n} 帧（共约 {seconds:.1f} 秒）。
素材标注的商品：{product}。

只记录画面里看得见的东西：主体、数量、动作和变化、可见的颜色纹理部位、环境和人的动作、画面里已有的文字。
不要推测新鲜度、口味、肉质、产地、到货时间、重量、价格；看不清就留空，不要编。每条用自然口语，尽量具体
（“蟹腿一节节往前挪”好过“很有活力”）。
伤痕、死伤、脏污、反光等负面观察只写进 issue，不要写进 actions、details、highlight、curiosity。

只输出一个 JSON 对象：
{{"subject": "画面主体，如 红帝王蟹", "matches_label": true/false/null（主体是否就是标注的商品，看不出填 null）,
 "count": "单只/两三只/一池/一箱 等", "shot_size": "特写/近景/中景/全景",
 "actions": ["最多3条，镜头里发生的动作或变化"], "details": ["最多4条，看得见的颜色、纹理、部位"],
 "setting": "水池/冰台/案板/秤/泡沫箱/手里 等", "people": "人或手在做什么，没有就留空",
 "highlight": "这个镜头最抓眼的一个瞬间，20字内", "curiosity": ["最多2条，观众看到这里可能会好奇的问题"],
 "text_on_screen": "画面里已有的文字，没有就留空", "usable": true/false, "issue": "模糊/遮挡/过曝 等，没有就留空"}}"""


def wanted() -> bool:
    return get_settings().vision_describe and configured()


def configured() -> bool:
    try:
        llm.vision_model()
    except llm.LLMError:
        return False
    return True


def _clean(text, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip(" ，。,.")
    if not text or UNPROVABLE.search(text) or NEGATIVE.search(text) or lint.lint(text):
        return ""
    return text[:limit]


def _clean_list(values, limit: int, count: int) -> list[str]:
    out = []
    for v in values if isinstance(values, list) else []:
        v = _clean(v, limit)
        if v and v not in out:
            out.append(v)
    return out[:count]


def normalise(data: dict) -> dict:
    """Keep only well-formed, picture-provable fields."""
    matches = data.get("matches_label")
    size = str(data.get("shot_size") or "")
    return {"subject": _clean(data.get("subject"), 16), "matches_label": matches if isinstance(matches, bool) else None,
            "count": _clean(data.get("count"), 8), "shot_size": size if size in SHOT_SIZES else "",
            "actions": _clean_list(data.get("actions"), 24, 3), "details": _clean_list(data.get("details"), 24, 4),
            "setting": _clean(data.get("setting"), 12), "people": _clean(data.get("people"), 24),
            "highlight": _clean(data.get("highlight"), 20), "curiosity": _clean_list(data.get("curiosity"), 24, 2),
            "text_on_screen": str(data.get("text_on_screen") or "")[:40],
            "usable": data.get("usable") is not False, "issue": str(data.get("issue") or "")[:16]}


def _parse(text: str) -> dict:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        raise ValueError("vision model returned no JSON")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("vision model returned no JSON object")
    return data


def describe_shot(path: str | Path, start: float, end: float, product: str, frames: int = 3) -> dict:
    """Vision notes for one shot of a (proxy) video file."""
    model = llm.vision_model()
    n = max(1, frames)
    content: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(n):
            t = start + (end - start) * (i + 0.5) / n
            jpg = F.extract_frame(path, t, Path(tmp) / f"{i}.jpg", 640)
            content.append({"type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpg.read_bytes()).decode()}})
    content.append({"type": "text", "text": _PROMPT.format(n=n, seconds=end - start, product=product or "未标注")})
    messages = [{"role": "user", "content": content}]
    try:
        text = model.chat(messages, json_mode=True, max_tokens=1200, temperature=0.2)
    except llm.LLMError as e:
        if "response_format" not in str(e):
            raise
        text = model.chat(messages, max_tokens=1200, temperature=0.2)  # some endpoints reject JSON mode with images
    return normalise(_parse(text))


def current(analysis: dict | None) -> dict | None:
    vision = (analysis or {}).get("vision") or {}
    return vision if vision.get("version") == VERSION else None


def describe_material(db: Session, material, progress=None, force: bool = False) -> int:
    """Log every shot of a material. Returns the number of shots described. Failures are recorded, not raised,
    unless every shot failed (then the job retries later)."""
    s = get_settings()
    if not force and current(material.analysis) and not current(material.analysis).get("errors"):
        return 0
    path = s.abs(material.proxy_path)
    shots = [(sh.start, sh.end) for sh in material.shots]

    def one(span):
        try:
            return {"start": span[0], "end": span[1],
                    **describe_shot(path, span[0], span[1], material.product, s.vision_frames_per_shot)}
        except (llm.LLMError, F.MediaError, ValueError) as e:
            log.warning("vision notes failed for %s %.1f-%.1f: %s", material.id, span[0], span[1], e)
            return {"start": span[0], "end": span[1], "error": str(e)[:300]}

    notes = []
    with ThreadPoolExecutor(max_workers=max(1, s.vision_concurrency)) as pool:
        for i, note in enumerate(pool.map(one, shots)):
            notes.append(note)
            if progress:
                progress((i + 1) / max(1, len(shots)), f"vision {i + 1}/{len(shots)}")
    errors = sum("error" in n for n in notes)
    if shots and errors == len(shots):
        raise llm.LLMError(f"vision model failed on every shot: {notes[0]['error']}")
    model = llm.vision_model()
    material.analysis = {**(material.analysis or {}), "vision": {
        "version": VERSION, "model": model.name, "shots": notes, "errors": errors}}
    db.commit()
    return len(notes) - errors


def ensure(db: Session, materials, progress=None) -> list[str]:
    """Log the shots of any material without current notes (first render after import, or after a prompt
    change). Returns warnings; never raises, because a render must not fail on copy grounding."""
    if not wanted():
        return [] if configured() else ["视觉模型未配置，文案无法依据画面生成"]
    missing = [m for m in materials if not current(m.analysis)]
    warnings = []
    for i, m in enumerate(missing):
        try:
            describe_material(db, m)
        except (llm.LLMError, F.MediaError, ValueError) as e:
            db.rollback()
            warnings.append(f"素材 {m.filename} 的画面场记失败：{e}")
        if progress:
            progress((i + 1) / len(missing), f"vision notes {i + 1}/{len(missing)}")
    return warnings


def notes_for(analysis: dict | None, start: float, end: float) -> dict | None:
    """Notes of the logged shot that overlaps [start, end] the most."""
    vision = current(analysis)
    if not vision:
        return None
    best, overlap = None, 0.0
    for note in vision.get("shots", []):
        ov = min(end, note["end"]) - max(start, note["start"])
        if ov > overlap and "error" not in note:
            best, overlap = note, ov
    if not best:
        return None
    return {k: v for k, v in best.items() if k not in ("start", "end", "usable", "issue", "text_on_screen") and (v or v is False)}
