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
from ..creative import vision
from ..creative.copywriter import ProductInfo
from ..creative.templates import TEMPLATES, eligible
from ..db import utcnow
from ..dedup import engine as dedup
from ..dedup import provenance
from ..media import ffmpeg as F
from ..models import BgmTrack, Material, Project, Variant
from ..render.base import RenderError
from ..render.ffmpeg_backend import get_backend
from ..quotes import outro as quote_outro
from ..quotes import service as quote_service
from .ingest import material_file, sha256_file

log = logging.getLogger(__name__)


class GenerationError(RuntimeError):
    pass


HARD_LIMITS = ("max_pair", "max_opening")  # L1 gates that stop a render


def pick_template(db: Session, project: Project, platform: str, rng: random.Random) -> str:
    # The old project-level price is unverified free text. Keep price-led videos in the reviewed quote series.
    safe = [t for t in eligible(platform) if t.id != "deal_promo"]
    allowed = [t for t in safe if not project.options.get("templates")
               or t.id in project.options["templates"]] or safe
    last_own = db.scalar(select(Variant.template_id).where(Variant.project_id == project.id,
                    Variant.platform == platform, Variant.template_id != "")
                         .order_by(Variant.seq.desc()).limit(1))
    if last_own:
        allowed = [t for t in allowed if t.id != last_own]
        if not allowed:
            raise GenerationError("该平台可用叙事模板只有一种，连续生成会重复；请增加模板")
    recent = db.scalars(select(Variant.template_id).where(Variant.platform == platform, Variant.template_id != "")
                        .order_by(Variant.created_at.desc()).limit(30)).all()
    uses = Counter(recent[:12])  # recent history weighs most
    own = Counter(db.scalars(select(Variant.template_id).where(Variant.project_id == project.id)).all())
    # Complete a project-level template rotation before repeating a narrative.
    least_used = min(own[t.id] for t in allowed)
    candidates = [t for t in allowed if own[t.id] == least_used]
    scored = [(t.platform_weight.get(platform, 1.0) / (1 + uses[t.id]) * rng.uniform(0.85, 1.15), t.id)
              for t in candidates]
    return max(scored)[1]


def recent_hooks(db: Session, limit: int = 60) -> set[str]:
    rows = db.scalars(select(Variant.copywriting)
                      .order_by(Variant.created_at.desc()).limit(limit)).all()
    hooks: set[str] = set()
    for c in rows:
        for k in ("hook_key", "hook"):
            if (c or {}).get(k):
                hooks.add(c[k])
    return hooks


def recent_copy(db: Session, limit: int = 40) -> list[str]:
    """On-screen and post text of the newest variants (all platforms), newest first, for novelty checks."""
    out: list[str] = []
    for v in db.scalars(select(Variant).where(Variant.template_id != "quote_series")
                        .order_by(Variant.created_at.desc()).limit(limit)):
        copy = v.copywriting or {}
        out += [copy.get("hook", ""), copy.get("title", "")]
        out += [t.get("text", "") for t in (v.plan or {}).get("texts", []) if t.get("role") in ("caption", "cta")]
    return [t.replace("\n", "") for t in out if t]


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
    template = TEMPLATES[variant.template_id or pick_template(db, project, variant.platform, rng)]
    ledger = dedup.usage_ledger(db, variant.platform)
    product = ProductInfo.from_dict(project.product)
    product.price = ""  # never feed unverified project text to the caption model or a video overlay
    quote = None
    if project.options.get("quote_sheet_id"):
        quote = quote_service.mixed_quote(db, project.options["quote_sheet_id"],
                                          project.options.get("quote_entry_id", ""),
                                          project.options.get("quote_revision", ""))
        views = [v for v in views if v.product == quote["product"]]
        if not views:
            raise GenerationError(f"没有「{quote['product']}」已分析完成的素材")
        # Free-form selling points, CTA and hashtags can smuggle a conflicting price
        # (including spellings such as “八十八块”). A priced variant only uses reviewed
        # quote fields plus the store/city identity in the curated copy library.
        product = ProductInfo(name=quote["product"], items=[quote["product"]],
                              store=product.store, city=product.city)
        quote["placement"] = project.options.get("quote_placement", "auto")
    if not product.items:
        product.items = sorted({v.product for v in views if v.product})
    bgm = pick_bgm(db, project, rng)
    dur = project.options.get("duration")
    inp = P.PlanInput(platform=variant.platform, template=template, materials=views, product=product,
                      seed=variant.seed, usage=ledger, avoid_hooks=recent_hooks(db), bgm=bgm,
                      duration_range=tuple(dur) if dur else None, quote=quote,
                      reserved_style=(variant.plan_report or {}).get("reserved_style"))
    inp.avoid_styles = {v.plan.get("style", {}).get("key") for v in db.scalars(
        select(Variant).where(Variant.platform == variant.platform).order_by(Variant.created_at.desc()).limit(80))
        if v.id != variant.id and v.plan and v.plan.get("style", {}).get("key")}
    inp.avoid_styles.update(v.plan.get("style", {}).get("key") for v in db.scalars(
        select(Variant).where(Variant.project_id == project.id))
        if v.id != variant.id and v.plan and v.plan.get("style", {}).get("key"))
    limits = {"max_pair": s.plan_max_pair_overlap, "max_opening": s.plan_max_opening_overlap,
              "library": s.plan_max_library_overlap}
    attempts, best = [], None
    for attempt in range(s.max_plan_attempts):
        inp.seed = variant.seed + attempt * 7919
        try:
            plan = P.plan(inp, with_copy=False)  # copy is written once, for the plan that is kept
        except P.PlanError as e:
            attempts.append({"attempt": attempt, "error": str(e)})
            break
        l1 = dedup.plan_overlap(db, plan, variant.platform, variant.id)
        attempts.append({"attempt": attempt, "seed": inp.seed, **l1})
        ratios = {key: l1[key] / max(limit, 1e-6) for key, limit in limits.items()}
        # Prefer a plan that passes the hard gates, then the one with the least reuse overall.
        severity = (max(0.0, max(ratios[k] for k in HARD_LIMITS) - 1), max(ratios.values()), sum(ratios.values()))
        if best is None or severity < best[2]:
            best = (plan, l1, severity)
        if all(r <= 1 for r in ratios.values()):
            break
        # Steer the next attempt away from the worst overlapping prior edit.
        dominant = "max_opening" if l1["max_opening"] / max(limits["max_opening"], 1e-6) > l1["max_pair"] / max(
            limits["max_pair"], 1e-6) else "max_pair"
        other = db.get(Variant, l1[f"{dominant}_variant"])
        if other:  # accumulate, so a later attempt cannot drift back to footage an earlier one avoided
            inp.exclude = provenance.union_footprint([inp.exclude, provenance.footprint(other.plan)])
    if best is None:
        raise GenerationError(attempts[-1].get("error", "无法生成方案") if attempts else "无法生成方案")
    plan, l1, _ = best
    inp.seed, inp.recent_copy = plan["seed"], recent_copy(db)
    try:
        P.compose_copy(plan, inp)
    except P.PlanError as e:
        raise GenerationError(str(e)) from e
    outro = quote_outro.spec(db, project.options, plan["clips"], variant.seed)
    if outro is None and s.quote_outro_required:
        raise GenerationError(f"{quote_service.today()} 的报价单尚未导入或确认，混剪片尾无法贴当日报价")
    if outro:
        plan["quote_outro"] = outro
    # Library coverage only rises as a finite footage pool is reused, so it steers the search above and flags
    # the variant for review in the dedup decision (dedup_review_library); it never blocks rendering by itself.
    accepted = all(l1[key] <= limits[key] for key in HARD_LIMITS)
    report = {"template": template.id, "attempts": attempts, "l1": l1, "limits": limits, "accepted": accepted,
              "exhausted": not accepted, "library_over": l1["library"] > limits["library"]}
    return plan, report


def generate_variant(db: Session, variant: Variant, progress=None) -> Variant:
    s = get_settings()
    project = db.get(Project, variant.project_id)
    backend = get_backend()
    variant.status, variant.error = "planning", ""
    db.commit()
    # Copy is written from what each shot shows; log shots that have no vision notes yet.
    vision_warnings = vision.ensure(db, list(db.scalars(select(Material).where(
        Material.id.in_(project.material_ids or [""]), Material.status == "ready"))),
        (lambda f, m: progress(0.08 * f, m)) if progress else None)
    views = material_views(db, project, backend.name)
    plan, report = make_plan(db, variant, project, views)
    report["vision_warnings"] = vision_warnings
    variant.plan, variant.plan_report, variant.template_id = plan, report, plan["template_id"]
    variant.copywriting = {k: plan["copy"][k] for k in ("title", "description", "hashtags", "hook", "hook_key")}
    if report["exhausted"]:
        variant.status = "exhausted"
        l1 = report["l1"]
        variant.error = (f"素材新鲜度不足：整片重合 {l1['max_pair']:.0%}、开头重合 {l1['max_opening']:.0%}、"
                         f"素材库覆盖 {l1['library']:.0%}；请补拍新素材或导入更多素材后再生成")
        db.commit()
        return variant
    variant.status = "rendering"
    db.commit()

    out_dir = s.path("renders", project.id)
    cover_dir = s.path("covers", project.id)
    out_video, out_cover = out_dir / f"{variant.id}.mp4", cover_dir / f"{variant.id}.jpg"
    outro = plan.get("quote_outro")
    # With a quote outro the backend renders the edit alone; the slideshow is appended below.
    body_video = out_dir / f"{variant.id}.body.mp4" if outro else out_video
    try:
        res = backend.render(plan, body_video, out_cover, s.path("projects", project.id),
                             progress=(lambda f, m: progress(0.1 + 0.6 * f, m)) if progress else None)
    except (RenderError, F.MediaError) as e:
        if backend.name == "jianying" and s.render_backend == "auto":
            log.warning("剪映 me render failed, falling back to FFmpeg: %s", e)
            backend = get_backend("ffmpeg")
            plan = _with_proxy_paths(db, plan)
            res = backend.render(plan, body_video, out_cover, s.path("projects", project.id))
            res.warnings.insert(0, f"剪映 me 渲染失败，已改用 FFmpeg：{e}")
        else:
            raise
    fp = None
    if outro:
        if progress:
            progress(0.72, "quote outro")
        # Every video of the day shares the outro, so only the edit itself is fingerprinted.
        fp = dedup.compute(res.video)
        try:
            quote_outro.append(res.video, quote_outro.render_cards(db, outro), outro, plan, out_video)
        except ValueError as e:
            raise GenerationError(str(e)) from e
        finally:
            res.video.unlink(missing_ok=True)
        res.video = out_video
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
    if quote_service.carries_quote(plan):
        error = quote_service.validate_variant(db, variant)
        if error:
            variant.status, variant.error = "failed", f"渲染后报价已失效：{error}"
            db.commit()
            return variant
    if progress:
        progress(0.85, "dedup")
    fp = fp or dedup.compute(res.video)
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
            # Reserve the narrative while this batch is created, before parallel render
            # workers can independently choose the same template for two platforms.
            v.template_id = pick_template(db, project, p, random.Random(v.seed))
            existing = list(db.scalars(select(Variant).where(Variant.project_id == project.id)))
            used_styles = {key for old in existing for key in (
                (old.plan or {}).get("style", {}).get("key"),
                (old.plan_report or {}).get("reserved_style", {}).get("key")) if key}
            recent_styles = [style for old in sorted(existing, key=lambda x: x.seq, reverse=True)
                             if old.platform == p for style in (
                                 (old.plan or {}).get("style") or
                                 (old.plan_report or {}).get("reserved_style"),) if style]
            style_input = P.PlanInput(platform=p, template=TEMPLATES[v.template_id], materials=[],
                                      product=ProductInfo(), seed=v.seed, usage=None, avoid_styles=used_styles,
                                      recent_styles=recent_styles)
            try:
                v.plan_report = {"reserved_style": P.choose_style(style_input, random.Random(v.seed))}
            except P.PlanError as exc:
                raise GenerationError(str(exc)) from exc
            db.add(v)
            out.append(v)
            db.flush()
    db.flush()
    return out


def output_path(v: Variant) -> Path:
    return get_settings().abs(v.file_path)
