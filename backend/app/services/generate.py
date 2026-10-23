"""Variant generation: template rotation → plan → L1 provenance gate (re-plan away from reused footage) → render →
fingerprint → dedup decision. Variants of one project × platform are generated sequentially by one job so that
siblings are checked against each other."""

from __future__ import annotations

import logging
import random
from collections import Counter
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import PLATFORM_SPECS, get_settings
from ..creative import planner as P
from ..creative.copywriter import ProductInfo
from ..creative.templates import TEMPLATES, eligible
from ..db import utcnow
from ..dedup import engine as dedup
from ..dedup import provenance
from ..media import ffmpeg as F
from ..models import BgmTrack, Material, Project, Variant
from ..render.base import RenderError
from ..render.ffmpeg_backend import get_backend
from .ingest import material_file, sha256_file

log = logging.getLogger(__name__)


class GenerationError(RuntimeError):
    pass


def pick_template(db: Session, project: Project, platform: str, rng: random.Random) -> str:
    allowed = [t for t in eligible(platform) if not project.options.get("templates")
               or t.id in project.options["templates"]] or eligible(platform)
    recent = db.scalars(select(Variant.template_id).where(Variant.platform == platform, Variant.template_id != "")
                        .order_by(Variant.created_at.desc()).limit(30)).all()
    uses = Counter(recent[:12])  # recent history weighs most
    own = Counter(db.scalars(select(Variant.template_id).where(Variant.project_id == project.id,
                                                               Variant.platform == platform)).all())
    scored = [(t.platform_weight.get(platform, 1.0) / (1 + uses[t.id] + 2 * own[t.id]) * rng.uniform(0.85, 1.15), t.id)
              for t in allowed]
    return max(scored)[1]


def recent_hooks(db: Session, platform: str, limit: int = 60) -> set[str]:
    rows = db.scalars(select(Variant.copywriting).where(Variant.platform == platform)
                      .order_by(Variant.created_at.desc()).limit(limit)).all()
    hooks: set[str] = set()
    for c in rows:
        for k in ("hook_key", "hook"):
            if (c or {}).get(k):
                hooks.add(c[k])
    return hooks


def pick_bgm(db: Session, project: Project, rng: random.Random) -> P.BgmView | None:
    mode = project.options.get("audio_mode", "auto")  # auto | original | bgm
    if mode == "original":
        return None
    q = select(BgmTrack).where(BgmTrack.active.is_(True))
    if project.options.get("bgm_ids"):
        q = q.where(BgmTrack.id.in_(project.options["bgm_ids"]))
    tracks = list(db.scalars(q))
    if not tracks:
        return None
    if mode == "auto" and rng.random() < 0.3:
        return None  # keep some originals: the market's own sound is part of the appeal
    used = Counter(((v.plan.get("audio") or {}).get("bgm") or {}).get("bgm_id")
                   for v in db.scalars(select(Variant).order_by(Variant.created_at.desc()).limit(40)))
    t = min(tracks, key=lambda x: (used[x.id], rng.random()))
    return P.BgmView(t.id, str(get_settings().abs(t.storage_path)), t.duration, t.beats or [])


def material_views(db: Session, project: Project, backend_name: str) -> list[P.MaterialView]:
    s = get_settings()
    mats = list(db.scalars(select(Material).where(Material.id.in_(project.material_ids or []),
                                                  Material.status == "ready")))
    if not mats:
        raise GenerationError("项目没有已分析完成的素材")
    views = []
    for m in mats:
        path = material_file(m) if backend_name == "jianying" else s.abs(m.proxy_path)
        views.append(P.MaterialView(id=m.id, source_group=m.source_group or m.id, product=m.product, width=m.width,
                                    height=m.height, duration=m.duration, fps=m.fps if backend_name == "jianying" else 30,
                                    analysis=m.analysis, shots=[(sh.start, sh.end) for sh in m.shots], path=str(path)))
    return views


def make_plan(db: Session, variant: Variant, project: Project, views: list[P.MaterialView]) -> tuple[dict, dict]:
    """Plan with the L1 gate. Returns (plan, report)."""
    s = get_settings()
    rng = random.Random(variant.seed)
    template = TEMPLATES[pick_template(db, project, variant.platform, rng)]
    ledger = dedup.usage_ledger(db, variant.platform)
    product = ProductInfo.from_dict(project.product)
    if not product.items:
        product.items = sorted({v.product for v in views if v.product})
    bgm = pick_bgm(db, project, rng)
    dur = project.options.get("duration")
    inp = P.PlanInput(platform=variant.platform, template=template, materials=views, product=product,
                      seed=variant.seed, usage=ledger, avoid_hooks=recent_hooks(db, variant.platform), bgm=bgm,
                      duration_range=tuple(dur) if dur else None)
    attempts, best = [], None
    for attempt in range(s.max_plan_attempts):
        inp.seed = variant.seed + attempt * 7919
        try:
            plan = P.plan(inp)
        except P.PlanError as e:
            attempts.append({"attempt": attempt, "error": str(e)})
            break
        l1 = dedup.plan_overlap(db, plan, variant.platform, variant.id)
        attempts.append({"attempt": attempt, "seed": inp.seed, **l1})
        if best is None or l1["max_pair"] < best[1]["max_pair"]:
            best = (plan, l1)
        if l1["max_pair"] <= s.plan_max_pair_overlap:
            break
        # steer the next attempt away from the footage shared with the most similar history video
        other = db.get(Variant, l1["max_pair_variant"])
        if other:
            for g, ivs in provenance.footprint(other.plan).items():
                inp.exclude.setdefault(g, []).extend(ivs)
    if best is None:
        raise GenerationError(attempts[-1].get("error", "无法生成方案") if attempts else "无法生成方案")
    plan, l1 = best
    report = {"template": template.id, "attempts": attempts, "l1": l1,
              "accepted": l1["max_pair"] <= s.plan_max_pair_overlap,
              "exhausted": l1["max_pair"] >= s.dedup_reject_content}
    return plan, report


def generate_variant(db: Session, variant: Variant, progress=None) -> Variant:
    s = get_settings()
    project = db.get(Project, variant.project_id)
    backend = get_backend()
    views = material_views(db, project, backend.name)
    variant.status, variant.error = "planning", ""
    db.commit()
    plan, report = make_plan(db, variant, project, views)
    variant.plan, variant.plan_report, variant.template_id = plan, report, plan["template_id"]
    variant.copywriting = {k: plan["copy"][k] for k in ("title", "description", "hashtags", "hook", "hook_key")}
    if report["exhausted"]:
        variant.status = "exhausted"
        variant.error = (f"素材新鲜度不足：最优方案与历史作品的素材重合度仍为 {report['l1']['max_pair']:.0%}，"
                         "请补拍新素材或导入更多素材后再生成")
        db.commit()
        return variant
    variant.status = "rendering"
    db.commit()

    out_dir = s.path("renders", project.id)
    cover_dir = s.path("covers", project.id)
    out_video, out_cover = out_dir / f"{variant.id}.mp4", cover_dir / f"{variant.id}.jpg"
    try:
        res = backend.render(plan, out_video, out_cover, s.path("projects", project.id),
                             progress=(lambda f, m: progress(0.1 + 0.7 * f, m)) if progress else None)
    except (RenderError, F.MediaError) as e:
        if backend.name == "jianying" and s.render_backend == "auto":
            log.warning("剪映 me render failed, falling back to FFmpeg: %s", e)
            backend = get_backend("ffmpeg")
            plan = _with_proxy_paths(db, plan)
            res = backend.render(plan, out_video, out_cover, s.path("projects", project.id))
            res.warnings.insert(0, f"剪映 me 渲染失败，已改用 FFmpeg：{e}")
        else:
            raise
    info = F.probe(res.video)
    variant.render_backend = res.backend
    variant.render_seconds = round(res.seconds, 2)
    variant.file_path, variant.cover_path = s.rel(res.video), s.rel(res.cover)
    variant.editor_project_path = str(res.editor_project) if res.editor_project else ""
    variant.sha256, variant.size_bytes = sha256_file(res.video), res.video.stat().st_size
    variant.duration, variant.width, variant.height = info.duration, info.width, info.height
    variant.plan_report = {**report, "render_warnings": sorted(set(res.warnings))}
    variant.status = "checking"
    db.commit()
    if progress:
        progress(0.85, "dedup")
    fp = dedup.compute(res.video)
    dedup.save(db, "variant", variant.id, fp, platform=variant.platform)
    dedup.check_variant(db, variant, fp)
    variant.updated_at = utcnow()
    db.commit()
    return variant


def _with_proxy_paths(db: Session, plan: dict) -> dict:
    s = get_settings()
    out = {**plan, "clips": [dict(c) for c in plan["clips"]]}
    for c in out["clips"]:
        m = db.get(Material, c["material_id"])
        c["path"] = str(s.abs(m.proxy_path))
    return out


def recheck(db: Session, variant: Variant) -> None:
    row = db.scalar(select(dedup.Fingerprint).where(dedup.Fingerprint.owner_type == "variant",
                                                    dedup.Fingerprint.owner_id == variant.id))
    fp = dedup.load(row) if row else dedup.compute(get_settings().abs(variant.file_path))
    if not row:
        dedup.save(db, "variant", variant.id, fp, platform=variant.platform)
    dedup.check_variant(db, variant, fp)
    db.commit()


def new_variants(db: Session, project: Project, platforms: list[str], count: int) -> list[Variant]:
    out = []
    base = db.scalar(select(Variant.seq).where(Variant.project_id == project.id).order_by(Variant.seq.desc())) or 0
    for p in platforms:
        if p not in PLATFORM_SPECS:
            raise GenerationError(f"unknown platform {p}")
        for i in range(count):
            base += 1
            v = Variant(project_id=project.id, platform=p, seq=base, seed=random.SystemRandom().randint(1, 2**31 - 1),
                        status="queued")
            db.add(v)
            out.append(v)
    db.flush()
    return out


def output_path(v: Variant) -> Path:
    return get_settings().abs(v.file_path)
