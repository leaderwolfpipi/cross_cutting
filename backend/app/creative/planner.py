"""Creative planner: materials + template + usage history → an edit decision list (EDL v1, see docs/design.md §3).

Clip selection is freshness-first: every candidate window is scored by how well it fits its narrative role, its
picture quality, how rarely that stretch of footage was used before (decayed by age) and how much the plan already
leans on the same source. Candidates are sampled (softmax), not arg-maxed, so seeds produce genuinely different
edits; within one plan source windows never overlap.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from ..config import PLATFORM_SPECS
from ..media.analysis import window_stats
from . import textfit
from .copywriter import CopyRequest, ProductInfo, get_copywriter
from .templates import ROLE_FIT, Slot, Template

TRANSITION_DEFAULT_S = {
    "cross_dissolve": 0.5, "dip_to_black": 0.5, "dip_to_white": 0.5, "overexposure": 0.6, "dissolve": 0.8,
    "zoom_in": 0.5, "zoom_out": 0.5, "cam_left": 0.5, "cam_right": 0.5, "cam_up": 0.5, "cam_down": 0.5,
    "spin_cw": 0.6, "blur": 0.6, "radial_blur": 0.5, "light_leak": 1.0, "light_sweep": 0.8, "shutter": 0.5,
    "ripple": 1.0, "rgb_split": 0.5, "glitch": 0.5, "push_left": 0.5, "push_right": 0.5, "flip": 0.8,
    "cube": 0.8, "wipe_left": 0.6, "open_door": 0.8,
}
WINDOW_STEP = 0.25
EDGE_MARGIN = 0.15  # keep away from the first/last frames of a shot (cuts, flashes)


@dataclass
class MaterialView:
    id: str
    source_group: str
    product: str
    width: int
    height: int
    duration: float
    fps: float
    analysis: dict
    shots: list[tuple[float, float]]
    path: str  # file the renderer reads (original for 剪映 me, proxy for FFmpeg)


@dataclass
class BgmView:
    id: str
    path: str
    duration: float
    beats: list[float] = field(default_factory=list)


@dataclass
class PlanInput:
    platform: str
    template: Template
    materials: list[MaterialView]
    product: ProductInfo
    seed: int
    usage: object  # provenance.UsageLedger
    avoid_hooks: set[str] = field(default_factory=set)
    exclude: dict[str, list[tuple[float, float]]] = field(default_factory=dict)  # windows to avoid this attempt
    bgm: BgmView | None = None
    duration_range: tuple[float, float] | None = None


class PlanError(RuntimeError):
    pass


# ---------------------------------------------------------------------- slots
def expand_slots(t: Template, rng: random.Random, target: tuple[float, float]) -> list[tuple[Slot, float]]:
    out: list[tuple[Slot, float]] = []
    for slot in t.slots:
        for _ in range(rng.randint(*slot.count)):
            out.append((slot, rng.uniform(*slot.dur)))
    total = sum(d for _, d in out)
    lo, hi = target
    if total < lo:
        k = min(lo / total, 1.3)
        out = [(s, d * k) for s, d in out]
        filler = [s for s in t.slots if s.role in ("detail", "action", "showcase")] or list(t.slots)
        while sum(d for _, d in out) < lo:
            s = rng.choice(filler)
            out.insert(len(out) - 1, (Slot(s.role, s.dur, caption=None), rng.uniform(*s.dur)))
    elif total > hi:
        k = hi / total
        out = [(s, max(s.dur[0] * 0.8, d * k)) for s, d in out]
    return out


# ---------------------------------------------------------------------- framing (剪映 me semantics: scale 1 = fit)
def framing(m: MaterialView, cw: int, ch: int, rng: random.Random, t: Template, role: str) -> dict:
    fit = min(cw / m.width, ch / m.height)
    fill = max(cw / m.width, ch / m.height)
    fill_rel = fill / fit
    src_aspect, can_aspect = m.width / m.height, cw / ch
    mismatch = max(src_aspect, can_aspect) / min(src_aspect, can_aspect)
    if mismatch > 1.35 and rng.random() < 0.4:
        # very different aspect (landscape footage on a vertical canvas): show it whole over a blurred copy
        return {"mode": "fit", "scale": 1.0, "x": 0.5, "y": 0.5, "background": "blur"}
    zoom = 1.0
    if role != "ending" and rng.random() < t.punch_prob:
        zoom = rng.uniform(1.15, 1.45)
    scale = fill_rel * zoom
    iw, ih = m.width * fit * scale / cw, m.height * fit * scale / ch
    x_lo, x_hi = (1 - iw / 2, iw / 2) if iw > 1 else (0.5, 0.5)
    y_lo, y_hi = (1 - ih / 2, ih / 2) if ih > 1 else (0.5, 0.5)
    # pan to a third of the free range (left / centre / right of the subject), not a random pixel offset
    x = rng.choice([x_lo + (x_hi - x_lo) * f for f in (0.2, 0.5, 0.8)])
    y = y_lo + (y_hi - y_lo) * rng.uniform(0.35, 0.65)
    return {"mode": "fill", "scale": round(scale, 4), "zoom": round(zoom, 3), "x": round(x, 4), "y": round(y, 4),
            "background": None}


# ---------------------------------------------------------------------- selection
def _role_fit(role: str, st: dict) -> float:
    w = ROLE_FIT.get(role, ROLE_FIT["showcase"])
    return sum(st.get(k, 0.0) * v for k, v in w.items())


def _candidates(inp: PlanInput, role: str, src_len: float, taken: dict[str, list[tuple[float, float]]],
                plan_groups: list[str]) -> list[tuple[float, MaterialView, float, float]]:
    out = []
    for m in inp.materials:
        blocked = taken.get(m.source_group, []) + inp.exclude.get(m.source_group, [])
        for s0, s1 in m.shots:
            a_min, a_max = s0 + EDGE_MARGIN, s1 - EDGE_MARGIN - src_len
            if a_max < a_min:
                continue
            n = int((a_max - a_min) / WINDOW_STEP) + 1
            for k in range(n):
                a = a_min + k * WINDOW_STEP
                b = a + src_len
                if any(a < y + 0.3 and x - 0.3 < b for x, y in blocked):
                    continue
                st = window_stats(m.analysis, a, b)
                if st["info"] < 0.5:
                    continue
                fit = _role_fit(role, st)
                used = inp.usage.used(m.source_group, a, b)
                fresh = math.exp(-1.5 * used)
                same = plan_groups.count(m.source_group)
                diversity = 0.55 ** same * (0.5 if plan_groups and plan_groups[-1] == m.source_group else 1.0)
                score = (0.25 + max(0.0, fit)) * (0.4 + 0.6 * st["sharp"]) * fresh * diversity
                out.append((score, m, round(a, 3), round(b, 3)))
    return out


def _sample(cands: list, rng: random.Random, temperature: float = 0.07):
    cands = sorted(cands, key=lambda c: -c[0])[:60]
    top = cands[0][0]
    weights = [math.exp((c[0] - top) / temperature) for c in cands]
    return rng.choices(cands, weights=weights, k=1)[0]


# ---------------------------------------------------------------------- beats
def snap_to_beats(durations: list[float], beats: list[float], tol: float = 0.25) -> list[float]:
    if not beats:
        return durations
    out, t, prev = [], 0.0, 0.0
    for d in durations[:-1]:
        t += d
        nearest = min(beats, key=lambda b: abs(b - t))
        cut = nearest if abs(nearest - t) <= tol and nearest - prev > 0.6 else t
        out.append(cut - prev)
        prev = cut
        t = cut
    out.append(max(0.8, sum(durations) - prev))
    return out


# ---------------------------------------------------------------------- main
def plan(inp: PlanInput) -> dict:
    spec = PLATFORM_SPECS[inp.platform]
    cw, ch = spec["width"], spec["height"]
    t = inp.template
    rng = random.Random(inp.seed)
    slots = expand_slots(t, rng, inp.duration_range or spec["target_duration"])
    durations = snap_to_beats([d for _, d in slots], inp.bgm.beats if inp.bgm else [])

    taken: dict[str, list[tuple[float, float]]] = {}
    groups: list[str] = []
    clips: list[dict] = []
    for (slot, _), out_d in zip(slots, durations):
        speed = rng.uniform(*t.speed)
        if slot.role == "detail" and rng.random() < t.slowmo_prob:
            speed = rng.uniform(0.6, 0.75)
        cands = []
        d = out_d
        for _ in range(4):  # shrink the clip if no window is long enough
            cands = _candidates(inp, slot.role, d * speed, taken, groups)
            if cands:
                break
            d *= 0.75
        if not cands:
            continue
        _, m, a, b = _sample(cands, rng)
        if speed < 0.95 and m.fps < 45:  # real slow motion needs high-frame-rate footage
            speed = max(speed, 0.9)
            b = round(min(a + d * speed, b), 3)
        taken.setdefault(m.source_group, []).append((a, b))
        groups.append(m.source_group)
        st = window_stats(m.analysis, a, b)
        fr = framing(m, cw, ch, rng, t, slot.role)
        clip = {"material_id": m.id, "source_group": m.source_group, "product": m.product, "path": m.path,
                "src_in": a, "src_out": b, "speed": round(speed, 3), "out_duration": round((b - a) / speed, 3),
                "role": slot.role, "caption_key": slot.caption, "label_key": slot.label,
                "transform": {k: fr[k] for k in ("mode", "scale", "x", "y")}, "background": fr["background"],
                "stats": {k: round(v, 3) for k, v in st.items()}}
        if rng.random() < t.combo_prob and fr["mode"] == "fill":
            clip["animation"] = {"combo": rng.choice(t.combos)}
        elif slot.role == "hook" or rng.random() < 0.25:
            clip["animation"] = {"in": rng.choice(t.entrances)}
        else:
            clip["animation"] = {}
        clips.append(clip)
    if len(clips) < 3:
        raise PlanError("素材不足：可用镜头少于 3 个（请补充素材或放宽查重排除区间）")

    # transitions on cuts: some hard cuts, otherwise a template transition, never the same kind twice in a row
    prev_kind = None
    for a_clip, b_clip in zip(clips, clips[1:]):
        if rng.random() < t.hard_cut_ratio:
            a_clip["transition_out"] = None
            continue
        kinds = [k for k in t.transitions if k != prev_kind]
        kind = rng.choice(kinds)
        dur = min(TRANSITION_DEFAULT_S.get(kind, 0.5), 0.4 * min(a_clip["out_duration"], b_clip["out_duration"]))
        a_clip["transition_out"] = {"kind": kind, "duration": round(dur, 3)}
        prev_kind = kind
    clips[-1]["transition_out"] = None

    # colour: one look for the whole video, lightly varied
    look = {"filter": rng.choice(t.filters), "saturation": round(rng.uniform(1.0, 1.12), 3),
            "temperature": round(rng.uniform(-0.05, 0.15), 3)}
    for c in clips:
        c["color"] = look

    # audio
    bgm = None
    if inp.bgm:
        bgm = {"bgm_id": inp.bgm.id, "path": inp.bgm.path, "volume_db": t.bgm_db,
               "src_in": round(rng.uniform(0, max(0.0, inp.bgm.duration - sum(c["out_duration"] for c in clips)) * 0.3), 2)}
    for c in clips:
        c["volume_db"] = t.original_db_with_bgm if bgm else t.original_db

    # copy + on-screen text
    req = CopyRequest(product=inp.product, template_id=t.id, platform=inp.platform, seed=inp.seed,
                      avoid_hooks=inp.avoid_hooks,
                      slots=[{"caption": c["caption_key"], "label": c["label_key"], "item": c["product"]} for c in clips])
    copy = get_copywriter().compose(req)
    texts = build_texts(clips, copy, t, cw, ch, rng)

    total = sum(c["out_duration"] for c in clips)
    hook_clip = clips[0]
    return {
        "version": 1, "template_id": t.id, "seed": inp.seed, "platform": inp.platform,
        "canvas": {"width": cw, "height": ch, "fps": spec["fps"]},
        "clips": clips, "texts": texts, "audio": {"bgm": bgm},
        "cover": {"time": round(min(1.4, hook_clip["out_duration"] * 0.6), 2)},
        "duration": round(total, 3), "copy": copy.to_dict(),
    }


def build_texts(clips: list[dict], copy, t: Template, cw: int, ch: int, rng: random.Random) -> list[dict]:
    texts: list[dict] = []
    starts, acc = [], 0.0
    for c in clips:
        starts.append(acc)
        acc += c["out_duration"]
    total = acc
    # hook: big title over the opening, upper third
    hook_dur = min(total, max(2.4, clips[0]["out_duration"]))
    body, size = textfit.fit(copy.hook, cw, ch, size=(0.05, 0.072), max_lines=2)
    texts.append({"role": "hook", "text": body, "start": 0.1, "duration": round(hook_dur - 0.1, 3),
                  "preset": rng.choice(t.hook_presets), "anim_in": rng.choice(t.hook_anim), "anim_out": "fade",
                  "style": {"size": size, "position_y": round(rng.uniform(0.2, 0.27), 3)}})
    cap_preset = rng.choice(t.caption_presets)
    label_preset = rng.choice(t.label_presets)
    for i, c in enumerate(clips):
        cap = copy.captions[i] if i < len(copy.captions) else None
        if cap and i > 0:  # the hook clip carries the title only
            is_cta = c["caption_key"] in ("cta", "soft_cta")
            body, size = textfit.fit(cap, cw, ch, size=(0.045, 0.06) if is_cta else (0.034, 0.048), max_lines=2)
            start = max(starts[i] + 0.15, 0.0 if is_cta else hook_dur)  # captions wait for the title to leave
            dur = starts[i] + c["out_duration"] - start - 0.1
            if dur >= 0.8:
                texts.append({"role": "cta" if is_cta else "caption", "text": body, "start": round(start, 3),
                              "duration": round(dur, 3),
                              "preset": rng.choice(t.hook_presets) if is_cta else cap_preset,
                              "anim_in": "pop" if is_cta else rng.choice(["slide_up", "fade"]), "anim_out": "fade",
                              "style": {"size": size, "position_y": 0.6 if is_cta else round(rng.uniform(0.76, 0.8), 3)}})
        label = copy.labels[i] if i < len(copy.labels) else None
        if label and c["out_duration"] >= 1.0:
            body, size = textfit.fit(label, cw, ch, size=(0.034, 0.042), max_lines=1)
            texts.append({"role": "label", "text": body, "start": round(starts[i] + 0.1, 3),
                          "duration": round(c["out_duration"] - 0.2, 3), "preset": label_preset, "anim_in": "pop",
                          "anim_out": "fade",
                          "style": {"size": size, "position_x": rng.choice([0.22, 0.78]), "position_y": 0.12}})
    return texts
