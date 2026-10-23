"""
数据库模型 - SQLAlchemy ORM
"""
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Text, JSON, ForeignKey, Index
)
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime
import uuid

Base = declarative_base()


def gen_uuid():
    return str(uuid.uuid4())


class Material(Base):
    """原始素材"""
    __tablename__ = "materials"

    id = Column(String, primary_key=True, default=gen_uuid)
    filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    file_size = Column(Integer, default=0)
    duration = Column(Float, default=0.0)
    width = Column(Integer, default=0)
    height = Column(Integer, default=0)
    fps = Column(Float, default=25.0)
    codec = Column(String, default="")
    sha256 = Column(String, unique=True, index=True)
    source_type = Column(String, default="self_shot")  # self_shot/purchased/authorized
    license_info = Column(JSON, default=dict)  # 授权信息
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="active")  # active/blacklisted
    tags = Column(JSON, default=list)
    description = Column(Text, default="")

    shots = relationship("Shot", back_populates="material", cascade="all, delete-orphan")


class Shot(Base):
    """镜头片段 - 从素材中切分出来的语义单元"""
    __tablename__ = "shots"

    id = Column(String, primary_key=True, default=gen_uuid)
    material_id = Column(String, ForeignKey("materials.id"), nullable=False)
    start_time = Column(Float, nullable=False)
    end_time = Column(Float, nullable=False)
    duration = Column(Float, nullable=False)
    motion_score = Column(Float, default=0.0)  # 运动强度
    quality_score = Column(Float, default=0.0)  # 质量评分
    has_text = Column(Boolean, default=False)
    scene_type = Column(String, default="unknown")  # close_up/cooking/result/process/talk
    thumbnail_path = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    material = relationship("Material", back_populates="shots")


class Project(Base):
    """剪辑项目"""
    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    template_id = Column(String, default="")  # 使用的创意模板
    creative_seed = Column(Integer, default=42)  # 创意随机种子
    selected_material_ids = Column(JSON, default=list)  # 选中的素材ID
    selected_shot_ids = Column(JSON, default=list)  # 选中的镜头ID
    bgm_id = Column(String, default="")
    script = Column(Text, default="")  # 口播/字幕脚本
    status = Column(String, default="draft")  # draft/processing/ready/reviewing/published/failed
    dedup_passed = Column(Boolean, default=False)
    similarity_score = Column(Float, default=0.0)
    error_message = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    variants = relationship("Variant", back_populates="project", cascade="all, delete-orphan")


class Variant(Base):
    """平台派生版本"""
    __tablename__ = "variants"

    id = Column(String, primary_key=True, default=gen_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    platform = Column(String, nullable=False)  # douyin/kuaishou/video_channel/xiaohongshu
    file_path = Column(String, default="")
    file_size = Column(Integer, default=0)
    width = Column(Integer, default=0)
    height = Column(Integer, default=0)
    duration = Column(Float, default=0.0)
    cover_path = Column(String, default="")
    title = Column(String, default="")
    description = Column(Text, default="")
    tags = Column(JSON, default=list)
    sha256 = Column(String, default="")
    status = Column(String, default="pending")  # pending/ready/approved/rejected/publishing/published/failed
    review_notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="variants")
    deliveries = relationship("Delivery", back_populates="variant", cascade="all, delete-orphan")


class Delivery(Base):
    """发布任务"""
    __tablename__ = "deliveries"

    id = Column(String, primary_key=True, default=gen_uuid)
    variant_id = Column(String, ForeignKey("variants.id"), nullable=False)
    platform = Column(String, nullable=False)
    account_id = Column(String, default="")  # 目标账号
    platform_video_id = Column(String, default="")  # 平台返回的ID
    status = Column(String, default="pending")  # pending/authorized/uploading/auditing/published/failed/takedown
    error_message = Column(Text, default="")
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    published_url = Column(String, default="")
    scheduled_at = Column(DateTime, nullable=True)
    published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    variant = relationship("Variant", back_populates="deliveries")


class Fingerprint(Base):
    """视频指纹"""
    __tablename__ = "fingerprints"

    id = Column(String, primary_key=True, default=gen_uuid)
    variant_id = Column(String, default="")  # 关联成片
    material_id = Column(String, default="")  # 或关联素材
    full_hash = Column(String, default="")  # 全片感知哈希
    frame_hashes = Column(JSON, default=list)  # 帧级哈希序列
    frame_times = Column(JSON, default=list)  # 对应时间戳
    segment_list = Column(JSON, default=list)  # 片段清单 [(material_id, shot_id, start, end, speed, mirror)]
    audio_fingerprint = Column(String, default="")  # 音频指纹
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_full_hash", "full_hash"),
        Index("idx_variant", "variant_id"),
    )


class SimilarityResult(Base):
    """相似度检测结果"""
    __tablename__ = "similarity_results"

    id = Column(String, primary_key=True, default=gen_uuid)
    source_id = Column(String, nullable=False)  # 当前视频
    target_id = Column(String, nullable=False)  # 历史视频
    full_hash_distance = Column(Float, default=0.0)
    segment_coverage = Column(Float, default=0.0)
    visual_score = Column(Float, default=0.0)
    audio_score = Column(Float, default=0.0)
    decision_score = Column(Float, default=0.0)
    decision = Column(String, default="pass")  # pass/review/reject
    details = Column(JSON, default=dict)
    checked_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_source", "source_id"),
        Index("idx_decision", "decision"),
    )


class PlatformAccount(Base):
    """平台账号授权"""
    __tablename__ = "platform_accounts"

    id = Column(String, primary_key=True, default=gen_uuid)
    platform = Column(String, nullable=False)
    account_name = Column(String, default="")
    account_id = Column(String, default="")
    access_token = Column(Text, default="")
    refresh_token = Column(Text, default="")
    token_expires_at = Column(DateTime, nullable=True)
    open_id = Column(String, default="")
    scope = Column(String, default="")
    status = Column(String, default="active")  # active/expired/revoked
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PublishTask(Base):
    """一键发布任务"""
    __tablename__ = "publish_tasks"

    id = Column(String, primary_key=True, default=gen_uuid)
    project_id = Column(String, default="")
    platform_configs = Column(JSON, default=list)  # [{platform, account_id, title, description, tags, scheduled_at}]
    status = Column(String, default="pending")  # pending/running/completed/partial/failed
    progress = Column(Integer, default=0)  # 0-100
    total_platforms = Column(Integer, default=0)
    completed_platforms = Column(Integer, default=0)
    results = Column(JSON, default=list)  # 各平台发布结果
    error_message = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class BGMAsset(Base):
    """BGM素材库"""
    __tablename__ = "bgm_assets"

    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    file_path = Column(String, default="")
    duration = Column(Float, default=0.0)
    license_info = Column(JSON, default=dict)
    beats = Column(JSON, default=list)  # 节拍点时间戳
    mood = Column(String, default="upbeat")  # upbeat/calm/epic/funny
    created_at = Column(DateTime, default=datetime.utcnow)
