"""ORM models (SQLAlchemy 2.0). Paths are stored relative to settings.data_dir."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .db import utcnow


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# ====================================================================== materials
class Material(TimestampMixin, Base):
    __tablename__ = "materials"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column(String(512))
    storage_path: Mapped[str] = mapped_column(String(1024))  # original file (relative to data dir, or absolute for imports)
    proxy_path: Mapped[str] = mapped_column(String(1024), default="")
    thumb_path: Mapped[str] = mapped_column(String(1024), default="")
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    width: Mapped[int] = mapped_column(Integer, default=0)  # display size (rotation applied)
    height: Mapped[int] = mapped_column(Integer, default=0)
    fps: Mapped[float] = mapped_column(Float, default=0.0)
    has_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    hdr: Mapped[str] = mapped_column(String(32), default="")  # "", "hlg", "pq"
    codec: Mapped[str] = mapped_column(String(32), default="")
    source_group: Mapped[str] = mapped_column(String(32), index=True, default="")
    product: Mapped[str] = mapped_column(String(128), default="")  # e.g. 红帝王蟹
    tags: Mapped[list[Any]] = mapped_column(default=list)
    license_type: Mapped[str] = mapped_column(String(32), default="self_shot")  # self_shot|purchased|authorized|unknown
    license_note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="uploaded")  # uploaded|analyzing|ready|failed|blacklisted
    error: Mapped[str] = mapped_column(Text, default="")
    analysis: Mapped[dict[str, Any]] = mapped_column(default=dict)  # per-frame features @ analysis_fps

    shots: Mapped[list[Shot]] = relationship(back_populates="material", cascade="all, delete-orphan",
                                             order_by="Shot.idx")

    @property
    def publishable(self) -> bool:
        return self.license_type in ("self_shot", "purchased", "authorized") and self.status != "blacklisted"


class Shot(Base):
    __tablename__ = "shots"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    material_id: Mapped[str] = mapped_column(ForeignKey("materials.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    motion: Mapped[float] = mapped_column(Float, default=0.0)
    sharpness: Mapped[float] = mapped_column(Float, default=0.0)
    vivid: Mapped[float] = mapped_column(Float, default=0.0)
    eye_catch: Mapped[float] = mapped_column(Float, default=0.0)
    tags: Mapped[list[Any]] = mapped_column(default=list)
    thumb_path: Mapped[str] = mapped_column(String(1024), default="")

    material: Mapped[Material] = relationship(back_populates="shots")

    @property
    def duration(self) -> float:
        return self.end - self.start


class BgmTrack(TimestampMixin, Base):
    __tablename__ = "bgm_tracks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(256))
    storage_path: Mapped[str] = mapped_column(String(1024))
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    bpm: Mapped[float] = mapped_column(Float, default=0.0)
    beats: Mapped[list[Any]] = mapped_column(default=list)
    mood: Mapped[str] = mapped_column(String(32), default="upbeat")
    license_note: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


# ====================================================================== projects & variants
class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(256))
    product: Mapped[dict[str, Any]] = mapped_column(default=dict)  # name, selling_points, origin, price, store, cta, keywords
    material_ids: Mapped[list[Any]] = mapped_column(default=list)
    platforms: Mapped[list[Any]] = mapped_column(default=list)
    options: Mapped[dict[str, Any]] = mapped_column(default=dict)  # templates, audio_mode, bgm_ids, duration override
    status: Mapped[str] = mapped_column(String(32), default="draft")  # draft|generating|ready|failed
    error: Mapped[str] = mapped_column(Text, default="")

    variants: Mapped[list[Variant]] = relationship(back_populates="project", cascade="all, delete-orphan",
                                                   order_by="Variant.created_at")


class Variant(TimestampMixin, Base):
    __tablename__ = "variants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    seed: Mapped[int] = mapped_column(Integer, default=0)
    template_id: Mapped[str] = mapped_column(String(64), default="")
    plan: Mapped[dict[str, Any]] = mapped_column(default=dict)  # EDL v1
    copywriting: Mapped[dict[str, Any]] = mapped_column(default=dict)  # title, description, tags, hook
    plan_report: Mapped[dict[str, Any]] = mapped_column(default=dict)  # L1 provenance check + attempts
    render_backend: Mapped[str] = mapped_column(String(32), default="")
    file_path: Mapped[str] = mapped_column(String(1024), default="")
    cover_path: Mapped[str] = mapped_column(String(1024), default="")
    editor_project_path: Mapped[str] = mapped_column(String(1024), default="")  # .jmeproj for manual fine-tuning
    sha256: Mapped[str] = mapped_column(String(64), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    # planned|rendering|checking|passed|review|rejected|approved|discarded|failed|exhausted
    status: Mapped[str] = mapped_column(String(32), default="planned", index=True)
    dedup_decision: Mapped[str] = mapped_column(String(16), default="")
    dedup_score: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str] = mapped_column(Text, default="")
    render_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    approved_by: Mapped[str] = mapped_column(String(128), default="")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped[Project] = relationship(back_populates="variants")

    @property
    def publishable(self) -> bool:
        return self.status in ("passed", "approved") and bool(self.file_path)


class HistoryItem(TimestampMixin, Base):
    """A video published outside this system, imported so new remixes are checked against it."""

    __tablename__ = "history_items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(512), default="")
    platform: Mapped[str] = mapped_column(String(32), default="")  # "" = all platforms
    storage_path: Mapped[str] = mapped_column(String(1024))
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending|ready|failed
    error: Mapped[str] = mapped_column(Text, default="")


class Fingerprint(Base):
    """Perceptual fingerprints of a variant, history item or material. Arrays are stored as raw bytes."""

    __tablename__ = "fingerprints"
    __table_args__ = (UniqueConstraint("owner_type", "owner_id", name="uq_fp_owner"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_type: Mapped[str] = mapped_column(String(16))  # variant|history|material
    owner_id: Mapped[str] = mapped_column(String(32), index=True)
    platform: Mapped[str] = mapped_column(String(32), default="", index=True)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    frame_step: Mapped[float] = mapped_column(Float, default=0.5)
    frame_hashes: Mapped[bytes] = mapped_column(LargeBinary, default=b"")  # uint64 little-endian
    frame_info: Mapped[bytes] = mapped_column(LargeBinary, default=b"")  # uint8 1 = informative frame
    audio_step: Mapped[float] = mapped_column(Float, default=0.0)
    audio_hashes: Mapped[bytes] = mapped_column(LargeBinary, default=b"")  # uint32 little-endian
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DedupReport(Base):
    __tablename__ = "dedup_reports"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    variant_id: Mapped[str] = mapped_column(ForeignKey("variants.id", ondelete="CASCADE"), index=True)
    decision: Mapped[str] = mapped_column(String(16))  # pass|review|reject
    score: Mapped[float] = mapped_column(Float, default=0.0)
    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    matches: Mapped[list[Any]] = mapped_column(default=list)
    scope: Mapped[str] = mapped_column(String(32), default="platform")
    compared: Mapped[int] = mapped_column(Integer, default=0)
    reviewed_by: Mapped[str] = mapped_column(String(128), default="")
    review_decision: Mapped[str] = mapped_column(String(16), default="")
    review_note: Mapped[str] = mapped_column(Text, default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


# ====================================================================== accounts & publishing
class Account(TimestampMixin, Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    display_name: Mapped[str] = mapped_column(String(256), default="")
    mode: Mapped[str] = mapped_column(String(16), default="api")  # api|assisted
    open_id: Mapped[str] = mapped_column(String(256), default="")
    access_token_enc: Mapped[str] = mapped_column(Text, default="")
    refresh_token_enc: Mapped[str] = mapped_column(Text, default="")
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scopes: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|needs_auth|disabled
    daily_limit: Mapped[int] = mapped_column(Integer, default=0)  # 0 = settings default
    min_interval_s: Mapped[int] = mapped_column(Integer, default=-1)  # -1 = settings default
    last_published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")


class OAuthState(Base):
    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(32))
    account_id: Mapped[str] = mapped_column(String(32), default="")  # re-authorising an existing account
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PublishBatch(TimestampMixin, Base):
    __tablename__ = "publish_batches"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    status: Mapped[str] = mapped_column(String(32), default="running")  # preflight_failed|running|completed|partial|failed
    preflight: Mapped[list[Any]] = mapped_column(default=list)
    requested_by: Mapped[str] = mapped_column(String(128), default="")
    summary: Mapped[dict[str, Any]] = mapped_column(default=dict)

    deliveries: Mapped[list[Delivery]] = relationship(back_populates="batch", order_by="Delivery.created_at")


class Delivery(TimestampMixin, Base):
    __tablename__ = "deliveries"
    __table_args__ = (UniqueConstraint("variant_id", "account_id", name="uq_delivery_variant_account"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(ForeignKey("publish_batches.id"), index=True)
    variant_id: Mapped[str] = mapped_column(ForeignKey("variants.id"), index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    mode: Mapped[str] = mapped_column(String(16), default="api")
    # queued|uploading|uploaded|creating|create_unknown|submitted|published|platform_rejected|
    # awaiting_operator|needs_auth|failed|canceled
    state: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    text: Mapped[str] = mapped_column(Text, default="")  # full caption sent to the platform (title + body + tags)
    marker: Mapped[str] = mapped_column(String(32), default="")  # short id embedded for reconciliation
    checkpoint: Mapped[dict[str, Any]] = mapped_column(default=dict)  # upload tokens, fragments done, video_id...
    platform_post_id: Mapped[str] = mapped_column(String(256), default="")
    share_url: Mapped[str] = mapped_column(String(1024), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_class: Mapped[str] = mapped_column(String(32), default="")
    last_error: Mapped[str] = mapped_column(Text, default="")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    operator: Mapped[str] = mapped_column(String(128), default="")

    batch: Mapped[PublishBatch] = relationship(back_populates="deliveries")
    events: Mapped[list[DeliveryEvent]] = relationship(back_populates="delivery", order_by="DeliveryEvent.at",
                                                      cascade="all, delete-orphan")

    TERMINAL = ("published", "platform_rejected", "failed", "canceled")


class DeliveryEvent(Base):
    __tablename__ = "delivery_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    delivery_id: Mapped[str] = mapped_column(ForeignKey("deliveries.id", ondelete="CASCADE"), index=True)
    from_state: Mapped[str] = mapped_column(String(32), default="")
    to_state: Mapped[str] = mapped_column(String(32), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    delivery: Mapped[Delivery] = relationship(back_populates="events")


class PlatformCircuit(Base):
    __tablename__ = "platform_circuits"

    platform: Mapped[str] = mapped_column(String(32), primary_key=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    open_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# ====================================================================== job queue
class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claim", "status", "queue", "run_after"),
        # at most one active job per dedupe key (partial unique index works on SQLite and PostgreSQL)
        Index("uq_jobs_active_dedupe", "dedupe_key", unique=True,
              sqlite_where=text("status IN ('queued','running') AND dedupe_key IS NOT NULL"),
              postgresql_where=text("status IN ('queued','running') AND dedupe_key IS NOT NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    queue: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|succeeded|failed|dead
    priority: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    locked_by: Mapped[str] = mapped_column(String(128), default="")
    dedupe_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    last_error: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
