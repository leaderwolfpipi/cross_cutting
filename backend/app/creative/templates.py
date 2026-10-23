"""Narrative templates. A template is a sequence of role slots plus the visual / pacing / text vocabulary the
planner may draw from. Ids of transitions, filters, animations and text presets are the 剪映 me catalogue ids
(the FFmpeg fallback renderer maps them to the closest effect it has)."""

from __future__ import annotations

from dataclasses import dataclass, field

# role → what the planner looks for in a window (weights over window_stats keys)
ROLE_FIT: dict[str, dict[str, float]] = {
    "hook": {"eye_catch": 0.6, "motion": 0.2, "vivid": 0.2},
    "detail": {"sharp": 0.5, "vivid": 0.35, "motion": -0.1, "exposure": 0.25},
    "action": {"motion": 0.7, "sharp": 0.2, "exposure": 0.1},
    "showcase": {"vivid": 0.45, "eye_catch": 0.35, "exposure": 0.2},
    "env": {"exposure": 0.4, "sharp": 0.3, "motion": 0.1, "vivid": 0.2},
    "ending": {"vivid": 0.4, "exposure": 0.3, "sharp": 0.3, "motion": -0.2},
}


@dataclass(frozen=True)
class Slot:
    role: str
    dur: tuple[float, float]  # output seconds per clip
    count: tuple[int, int] = (1, 1)
    caption: str | None = None  # copy-library key for the lower-third text over these clips
    label: str | None = None  # copy-library key for a corner badge


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    slots: tuple[Slot, ...]
    platform_weight: dict[str, float]
    transitions: tuple[str, ...]
    hard_cut_ratio: float
    filters: tuple[str, ...]
    hook_presets: tuple[str, ...]
    caption_presets: tuple[str, ...]
    label_presets: tuple[str, ...] = ("label_yellow", "news", "label_dark")
    combos: tuple[str, ...] = ("ken_burns", "zoom_out_slow", "breathe")
    entrances: tuple[str, ...] = ("zoom_in", "pop", "fade")
    punch_prob: float = 0.45  # chance a clip is re-framed with a punch-in
    combo_prob: float = 0.35  # chance a clip gets a whole-clip camera move
    speed: tuple[float, float] = (0.95, 1.08)
    slowmo_prob: float = 0.0  # chance a detail clip plays at 0.6–0.75×
    original_db: float = -3.0  # original sound level (no BGM)
    original_db_with_bgm: float = -16.0
    bgm_db: float = -9.0
    hook_anim: tuple[str, ...] = ("pop",)
    styles: dict = field(default_factory=dict)


TEMPLATES: dict[str, Template] = {t.id: t for t in (
    Template(
        id="hook_reveal", name="钩子揭晓",
        slots=(Slot("hook", (2.2, 3.0)), Slot("detail", (1.6, 2.6), (2, 3), caption="selling_point"),
               Slot("action", (1.8, 2.8), (1, 2), caption="selling_point"), Slot("showcase", (2.2, 3.2), label="badge"),
               Slot("ending", (2.4, 3.2), caption="cta")),
        platform_weight={"douyin": 1.0, "kuaishou": 1.0, "channels": 0.8, "xiaohongshu": 0.8},
        transitions=("zoom_in", "cam_left", "blur", "cross_dissolve", "rgb_split", "light_leak"),
        hard_cut_ratio=0.4, filters=("鲜艳", "暖阳", "清新"), hook_presets=("variety", "fire", "comic"),
        caption_presets=("classic", "shadow_bold"), slowmo_prob=0.25),
    Template(
        id="beat_montage", name="快剪卡点",
        slots=(Slot("hook", (1.2, 1.6)), Slot("action", (0.9, 1.4), (3, 4)), Slot("detail", (0.9, 1.3), (2, 3)),
               Slot("showcase", (1.0, 1.5), (2, 3), label="badge"), Slot("ending", (2.2, 2.8), caption="cta")),
        platform_weight={"douyin": 1.2, "kuaishou": 1.2, "channels": 0.5, "xiaohongshu": 0.5},
        transitions=("zoom_in", "glitch", "rgb_split", "spin_cw", "cam_right", "shutter", "flip"),
        hard_cut_ratio=0.55, filters=("鲜艳", "冷调", "胶片"), hook_presets=("comic", "fire", "cyber"),
        caption_presets=("shadow_bold", "classic"), combo_prob=0.15, punch_prob=0.6, speed=(1.0, 1.12),
        entrances=("zoom_in", "pop", "slide_up")),
    Template(
        id="arrival_story", name="到港实拍",
        slots=(Slot("env", (2.4, 3.2), caption="story_open"), Slot("hook", (2.0, 2.8)),
               Slot("detail", (2.0, 3.0), (2, 3), caption="selling_point"), Slot("showcase", (2.4, 3.4), label="badge"),
               Slot("ending", (2.6, 3.4), caption="cta")),
        platform_weight={"douyin": 0.9, "kuaishou": 0.9, "channels": 1.3, "xiaohongshu": 0.9},
        transitions=("cross_dissolve", "dip_to_black", "light_sweep", "cam_up", "blur"),
        hard_cut_ratio=0.35, filters=("胶片", "复古", "暖阳"), hook_presets=("gold", "shadow_bold", "variety"),
        caption_presets=("label_dark", "classic"), combo_prob=0.55, punch_prob=0.3, original_db=-1.0,
        hook_anim=("typewriter", "fade")),
    Template(
        id="buying_tips", name="挑选攻略",
        slots=(Slot("hook", (2.4, 3.0)), Slot("detail", (2.4, 3.2), caption="tip1"),
               Slot("detail", (2.4, 3.2), caption="tip2"), Slot("action", (2.2, 3.0), caption="tip3"),
               Slot("showcase", (2.2, 3.0), caption="tips_summary"), Slot("ending", (2.2, 2.8), caption="cta")),
        platform_weight={"douyin": 0.8, "kuaishou": 0.8, "channels": 1.2, "xiaohongshu": 1.4},
        transitions=("push_left", "wipe_left", "cross_dissolve", "zoom_in", "open_door"),
        hard_cut_ratio=0.3, filters=("清新", "日系", "鲜艳"), hook_presets=("label_yellow", "candy", "variety"),
        caption_presets=("label_dark", "news"), punch_prob=0.55, hook_anim=("pop", "slide_up")),
    Template(
        id="deal_promo", name="今日福利",
        slots=(Slot("hook", (2.0, 2.6)), Slot("showcase", (1.4, 2.0), (2, 3), caption="selling_point"),
               Slot("detail", (1.4, 2.0), (1, 2), label="badge"), Slot("action", (1.6, 2.2), caption="price"),
               Slot("ending", (2.4, 3.0), caption="cta")),
        platform_weight={"douyin": 1.1, "kuaishou": 1.3, "channels": 0.8, "xiaohongshu": 0.6},
        transitions=("zoom_in", "flip", "cube", "light_leak", "rgb_split"),
        hard_cut_ratio=0.45, filters=("鲜艳", "暖阳"), hook_presets=("fire", "gold", "variety"),
        caption_presets=("news", "label_yellow"), punch_prob=0.5),
    Template(
        id="asmr_immersive", name="沉浸式",
        slots=(Slot("hook", (2.6, 3.4)), Slot("detail", (3.0, 4.2), (2, 3)), Slot("action", (2.8, 4.0)),
               Slot("ending", (3.0, 3.8), caption="soft_cta")),
        platform_weight={"douyin": 0.8, "kuaishou": 0.6, "channels": 1.0, "xiaohongshu": 1.3},
        transitions=("cross_dissolve", "blur", "dissolve", "ripple"),
        hard_cut_ratio=0.3, filters=("日系", "清新", "高级灰"), hook_presets=("kaiti", "shadow_bold", "cute"),
        caption_presets=("label_dark", "classic"), combo_prob=0.7, punch_prob=0.35, speed=(0.9, 1.0), slowmo_prob=0.4,
        original_db=0.0, original_db_with_bgm=-10.0, bgm_db=-14.0, hook_anim=("fade", "typewriter")),
)}


def eligible(platform: str) -> list[Template]:
    return [t for t in TEMPLATES.values() if t.platform_weight.get(platform, 0) > 0]
