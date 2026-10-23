"""Portable FFmpeg render backend (Linux render nodes, CI, or when 剪映 me is unavailable).

Renders the same EDL v1 as the 剪映 me backend with the closest FFmpeg equivalents: per-clip segments (speed,
re-framing in 剪映 'scale 1 = fit' semantics, blurred-background fit, colour look, simple camera pan), xfade
transitions that keep the plan's timing (each transition borrows half its length from both neighbours, like
剪映), ASS text styled after the 剪映 花字 presets, BGM mix and -14 LUFS loudness normalisation.
Reads the normalised proxies (SDR, 30 fps), so it never decodes camera originals.
"""

from __future__ import annotations

import logging
import subprocess
import time
from functools import lru_cache
from pathlib import Path

from ..config import get_settings
from ..media import ffmpeg as F
from .base import Progress, RenderError, RenderResult

log = logging.getLogger(__name__)

XFADE = {
    "cross_dissolve": "fade", "color_dissolve": "fade", "dissolve": "dissolve", "dip_to_black": "fadeblack",
    "dip_to_white": "fadewhite", "overexposure": "fadewhite", "light_leak": "fadewhite", "blink": "fadeblack",
    "zoom_in": "zoomin", "zoom_out": "circleclose", "cam_left": "slideleft", "cam_right": "slideright",
    "cam_up": "slideup", "cam_down": "slidedown", "push_left": "slideleft", "push_right": "slideright",
    "push_up": "slideup", "push_down": "slidedown", "spin_cw": "radial", "spin_ccw": "radial", "spin": "radial",
    "clock_wipe": "radial", "swirl": "radial", "blur": "hblur", "radial_blur": "radial", "light_sweep": "smoothright",
    "shutter": "vertopen", "split_open": "vertopen", "open_door": "vertopen", "lines_v": "vuslice",
    "lines_h": "hlslice", "blinds": "hrslice", "diagonal_split": "diagtl", "ripple": "pixelize",
    "rgb_split": "pixelize", "glitch": "pixelize", "signal": "pixelize", "stretch": "squeezeh", "flip": "squeezeh",
    "cube": "coverleft", "circle_open": "circleopen", "heart": "circleopen", "star": "circleopen",
    "diamond": "circleopen", "wipe_left": "wipeleft", "wipe_right": "wiperight", "wipe_up": "wipeup",
    "wipe_down": "wipedown", "gradient_wipe": "smoothleft",
}
LOOKS = {
    "原图": "", "鲜艳": "eq=saturation=1.28:contrast=1.05", "暖阳": "colorbalance=rs=.06:gs=.02:bs=-.06,eq=saturation=1.1",
    "清新": "eq=brightness=0.03:saturation=1.08,colorbalance=bs=.03:gs=.01", "冷调": "colorbalance=rs=-.04:bs=.08",
    "黑白": "hue=s=0", "复古": "curves=preset=vintage,eq=saturation=0.9", "胶片": "curves=preset=medium_contrast,eq=saturation=0.95",
    "日系": "eq=brightness=0.04:contrast=0.92:saturation=0.9", "高级灰": "eq=saturation=0.75:contrast=1.05",
}


def _c(rgba: tuple[int, int, int, int]) -> str:
    r, g, b, a = rgba
    return f"&H{255 - a:02X}{b:02X}{g:02X}{r:02X}"


# preset → (fill, outline, outline_px_per_em, bold, italic, box_colour|None, shadow_px_per_em, blur)
PRESETS = {
    "classic": ((255, 255, 255, 255), (0, 0, 0, 255), 0.10, True, False, None, 0.0, 0),
    "variety": ((255, 221, 0, 255), (0, 0, 0, 255), 0.13, True, False, None, 0.05, 0),
    "cute": ((255, 255, 255, 255), (255, 105, 160, 255), 0.14, True, False, None, 0.0, 0),
    "neon_pink": ((255, 240, 250, 255), (255, 60, 180, 255), 0.08, False, False, None, 0.0, 3),
    "cyber": ((190, 250, 255, 255), (0, 160, 255, 255), 0.08, True, True, None, 0.0, 2),
    "fire": ((255, 150, 40, 255), (90, 15, 0, 255), 0.11, True, False, None, 0.05, 0),
    "candy": ((255, 160, 220, 255), (255, 255, 255, 255), 0.12, True, False, None, 0.0, 0),
    "gold": ((240, 200, 90, 255), (70, 40, 0, 255), 0.08, True, False, None, 0.04, 0),
    "label_dark": ((255, 255, 255, 255), (0, 0, 0, 200), 0.3, False, False, (0, 0, 0, 200), 0.0, 0),
    "label_yellow": ((20, 20, 20, 255), (255, 214, 0, 255), 0.3, True, False, (255, 214, 0, 255), 0.0, 0),
    "news": ((255, 255, 255, 255), (215, 30, 40, 240), 0.35, True, False, (215, 30, 40, 240), 0.0, 0),
    "shadow_bold": ((255, 255, 255, 255), (0, 0, 0, 0), 0.0, True, False, None, 0.07, 0),
    "hollow": ((255, 255, 255, 0), (255, 255, 255, 255), 0.04, True, False, None, 0.0, 1),
    "retro": ((255, 238, 205, 255), (0, 0, 0, 0), 0.0, False, False, None, 0.04, 1),
    "kaiti": ((255, 255, 255, 255), (40, 40, 40, 255), 0.07, False, False, None, 0.04, 0),
    "comic": ((255, 255, 255, 255), (230, 30, 50, 255), 0.14, True, True, None, 0.0, 0),
}


@lru_cache
def cjk_font() -> str:
    for name in ("Source Han Sans SC", "Noto Sans CJK SC", "PingFang SC", "Hiragino Sans GB", "WenQuanYi Zen Hei",
                 "Heiti SC"):
        try:
            out = subprocess.run(["fc-match", name, "family"], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.TimeoutExpired):
            break
        if name.lower() in out.lower():
            return name
    return "Sans"


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\n", "\\N")


def build_ass(texts: list[dict], w: int, h: int) -> str:
    font = cjk_font()
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 2",
             "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
             "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
             "MarginL, MarginR, MarginV, Encoding"]
    for name, (fill, outline, ow, bold, italic, box, sh, _) in PRESETS.items():
        size = 60
        border_style = 3 if box else 1
        lines.append(f"Style: {name},{font},{size},{_c(fill)},{_c(fill)},{_c(box or outline)},{_c((0, 0, 0, 150))},"
                     f"{-1 if bold else 0},{-1 if italic else 0},0,0,100,100,0,0,{border_style},{ow * size:.1f},"
                     f"{sh * size:.1f},5,0,0,0,1")
    lines += ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for i, t in enumerate(texts):
        st = t.get("style") or {}
        preset = t.get("preset") if t.get("preset") in PRESETS else "classic"
        fs = st.get("size", 0.05) * h * 1.12  # libass em box is smaller than 剪映 me's at the same nominal size
        ow = PRESETS[preset][2] * fs
        sh = PRESETS[preset][6] * fs
        x, y = st.get("position_x", 0.5) * w, st.get("position_y", 0.88) * h
        start, end = float(t["start"]), float(t["start"]) + float(t["duration"])
        fin = 250 if t.get("anim_in") in ("fade", "typewriter", "slide_up") else 0
        fout = 250 if t.get("anim_out") else 0
        tags = [f"\\fs{fs:.0f}", f"\\bord{ow:.1f}", f"\\shad{sh:.1f}", f"\\fad({fin},{fout})"]
        if PRESETS[preset][7]:
            tags.append(f"\\blur{PRESETS[preset][7]}")
        if t.get("anim_in") == "slide_up":
            tags.append(f"\\move({x:.0f},{y + 0.03 * h:.0f},{x:.0f},{y:.0f},0,280)")
        else:
            tags.append(f"\\pos({x:.0f},{y:.0f})")
        if t.get("anim_in") == "pop":
            tags.append("\\fscx40\\fscy40\\t(0,160,\\fscx112\\fscy112)\\t(160,260,\\fscx100\\fscy100)")
        lines.append(f"Dialogue: {10 + i},{_ass_time(start)},{_ass_time(end)},{preset},,0,0,0,,"
                     f"{{{''.join(tags)}}}{_esc(t['text'])}")
    return "\n".join(lines) + "\n"


class FFmpegBackend:
    name = "ffmpeg"

    def available(self) -> bool:
        return F.has_filter("xfade") and F.has_filter("ass")

    # ------------------------------------------------------------------ segments
    def _transform(self, c: dict, info: F.ProbeInfo, cw: int, ch: int, dur: float) -> str:
        tr = c.get("transform") or {}
        look = LOOKS.get((c.get("color") or {}).get("filter", ""), "")
        if c.get("background") == "blur" or tr.get("mode") == "fit":
            chain = (f"split[a][b];[a]scale={cw}:{ch}:force_original_aspect_ratio=increase,crop={cw}:{ch},"
                     f"boxblur=30:3,eq=brightness=-0.08[bg];[b]scale={cw}:{ch}:force_original_aspect_ratio=decrease[fg];"
                     f"[bg][fg]overlay=(W-w)/2:(H-h)/2")
        else:
            fit = min(cw / info.width, ch / info.height)
            scale = max(float(tr.get("scale", 1.0)), max(cw / info.width, ch / info.height) / fit)
            pan = (c.get("animation") or {}).get("combo") is not None
            if pan:
                scale *= 1.08  # room for a slow camera move
            iw, ih = max(cw, F.even(info.width * fit * scale)), max(ch, F.even(info.height * fit * scale))
            ox = min(max(0.0, iw / 2 - tr.get("x", 0.5) * cw), iw - cw)
            oy = min(max(0.0, ih / 2 - tr.get("y", 0.5) * ch), ih - ch)
            if pan:
                ox1 = min(max(0.0, ox + (iw - cw) * 0.5 * (1 if ox < (iw - cw) / 2 else -1)), iw - cw)
                xexpr = f"'{ox:.1f}+({ox1 - ox:.1f})*min(1,t/{max(dur, 0.1):.3f})'"
                chain = f"scale={iw}:{ih}:flags=bicubic,crop={cw}:{ch}:{xexpr}:{oy:.1f}"
            else:
                chain = f"scale={iw}:{ih}:flags=bicubic,crop={cw}:{ch}:{ox:.1f}:{oy:.1f}"
        return chain + (f",{look}" if look else "")

    def _segment(self, c: dict, head: float, tail: float, dst: Path, cw: int, ch: int, fps: int) -> float:
        src = c["path"]
        info = F.probe(src)
        speed = float(c.get("speed", 1.0))
        a = max(0.0, c["src_in"] - head * speed)
        b = min(info.duration, c["src_out"] + tail * speed)
        out_len = (b - a) / speed
        vchain = (f"[0:v]setpts=(PTS-STARTPTS)/{speed:.4f},{self._transform(c, info, cw, ch, out_len)},"
                  f"fps={fps},format=yuv420p,setsar=1[v]")
        tempo = speed
        atempo = []
        while tempo < 0.5 or tempo > 2.0:  # atempo accepts 0.5..2 per instance
            step = 0.5 if tempo < 0.5 else 2.0
            atempo.append(f"atempo={step}")
            tempo /= step
        atempo.append(f"atempo={tempo:.4f}")
        achain = (f"[0:a]asetpts=PTS-STARTPTS,{','.join(atempo)},volume={c.get('volume_db', 0.0):.1f}dB,"
                  f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a]")
        F.run([*F.ffmpeg_base(), "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", src, "-filter_complex",
               f"{vchain};{achain}", "-map", "[v]", "-map", "[a]", "-t", f"{out_len:.3f}", "-c:v", "libx264",
               "-preset", "veryfast", "-crf", "15", "-g", str(fps), "-c:a", "pcm_s16le", str(dst)],
              timeout=600, what="segment")
        return F.probe(dst).duration

    # ------------------------------------------------------------------ render
    def render(self, plan: dict, out_video: Path, out_cover: Path, work_dir: Path,
               progress: Progress | None = None) -> RenderResult:
        t0 = time.monotonic()
        work_dir.mkdir(parents=True, exist_ok=True)
        cw, ch, fps = plan["canvas"]["width"], plan["canvas"]["height"], int(plan["canvas"].get("fps", 30))
        clips = plan["clips"]
        trans = [(c.get("transition_out") or {}) for c in clips]
        segs, lens = [], []
        for i, c in enumerate(clips):
            head = (trans[i - 1].get("duration", 0.0) / 2) if i > 0 and trans[i - 1] else 0.0
            tail = (trans[i].get("duration", 0.0) / 2) if trans[i] and i < len(clips) - 1 else 0.0
            dst = work_dir / f"seg{i:02d}.mkv"
            lens.append(self._segment(c, head, tail, dst, cw, ch, fps))
            segs.append(dst)
            if progress:
                progress(0.6 * (i + 1) / len(clips), f"segment {i + 1}/{len(clips)}")

        # common timebase on every input and intermediate result: xfade refuses mismatched links
        parts = [f"[{k}:v]settb=AVTB,setpts=PTS-STARTPTS[i{k}v];[{k}:a]asetpts=PTS-STARTPTS[i{k}a]"
                 for k in range(len(segs))]
        v, a, running = "[i0v]", "[i0a]", lens[0]
        for k in range(1, len(segs)):
            tr = trans[k - 1]
            nv, na = f"[v{k}]", f"[a{k}]"
            kind = XFADE.get(tr.get("kind", ""), "fade") if tr else None
            if tr and kind:
                d = min(float(tr["duration"]), lens[k - 1] * 0.9, lens[k] * 0.9)
                parts.append(f"{v}[i{k}v]xfade=transition={kind}:duration={d:.3f}:offset={running - d:.3f},"
                             f"settb=AVTB{nv}")
                parts.append(f"{a}[i{k}a]acrossfade=d={d:.3f}{na}")
                running += lens[k] - d
            else:
                parts.append(f"{v}{a}[i{k}v][i{k}a]concat=n=2:v=1:a=1[c{k}v]{na};[c{k}v]settb=AVTB{nv}")
                running += lens[k]
            v, a = nv, na
        ass_path = work_dir / "texts.ass"
        ass_path.write_text(build_ass(plan.get("texts", []), cw, ch), encoding="utf-8")
        esc = str(ass_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        parts.append(f"{v}ass='{esc}'[vout]")
        inputs = [x for s in segs for x in ("-i", str(s))]
        bgm = (plan.get("audio") or {}).get("bgm")
        if bgm:
            inputs += ["-ss", f"{bgm.get('src_in', 0.0):.3f}", "-i", bgm["path"]]
            bi = len(segs)
            parts.append(f"[{bi}:a]atrim=0:{running:.3f},asetpts=PTS-STARTPTS,afade=t=in:d=0.3,"
                         f"afade=t=out:st={max(0.0, running - 1.2):.3f}:d=1.2,volume={bgm.get('volume_db', -9):.1f}dB,"
                         f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[bgm]")
            parts.append(f"{a}[bgm]amix=inputs=2:duration=first:normalize=0[mix]")
            a = "[mix]"
        parts.append(f"{a}loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")
        out_video.parent.mkdir(parents=True, exist_ok=True)
        if progress:
            progress(0.65, "assembling")
        F.run([*F.ffmpeg_base(), *inputs, "-filter_complex", ";".join(parts), "-map", "[vout]", "-map", "[aout]",
               "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-profile:v", "high", "-pix_fmt", "yuv420p",
               "-r", str(fps), "-g", str(fps * 2), "-color_primaries", "bt709", "-color_trc", "bt709",
               "-colorspace", "bt709", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart",
               str(out_video)], timeout=max(900, running * 30), what="assemble")
        cover_t = float(plan.get("cover", {}).get("time", 1.0))
        F.run([*F.ffmpeg_base(), "-ss", f"{cover_t:.3f}", "-i", str(out_video), "-frames:v", "1", "-q:v", "2",
               str(out_cover)], timeout=60, what="cover")
        for s in segs:
            s.unlink(missing_ok=True)
        warnings = [] if cjk_font() != "Sans" else ["未找到中文字体，字幕可能显示为方块：请安装 Noto Sans CJK / 思源黑体"]
        return RenderResult(video=out_video, cover=out_cover, backend=self.name, seconds=time.monotonic() - t0,
                            warnings=warnings)


def get_backend(prefer: str | None = None):
    """Pick the render backend: explicit setting, else 剪映 me when installed, else FFmpeg."""
    from .jianying import JianyingBackend

    choice = prefer or get_settings().render_backend
    if choice in ("jianying", "auto"):
        jy = JianyingBackend()
        if jy.available():
            return jy
        if choice == "jianying":
            raise RenderError("RENDER_BACKEND=jianying but 剪映 me is not available (set JIANYING_MCP_CMD)")
    fb = FFmpegBackend()
    if not fb.available():
        raise RenderError("FFmpeg lacks xfade/ass filters (needs FFmpeg ≥ 4.3 built with libass)")
    return fb
