"""
Pydantic 数据模型
"""
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime


# ====== 素材 ======
class MaterialCreate(BaseModel):
    filename: str
    file_path: str
    source_type: str = "self_shot"
    license_info: Dict = {}
    tags: List[str] = []
    description: str = ""


class MaterialResponse(BaseModel):
    id: str
    filename: str
    file_path: str
    file_size: int
    duration: float
    width: int
    height: int
    fps: float
    codec: str
    sha256: str
    source_type: str
    status: str
    tags: List[str]
    description: str
    shot_count: int = 0


# ====== 项目 ======
class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    template_id: str = ""
    selected_material_ids: List[str] = []
    selected_shot_ids: List[str] = []
    bgm_id: str = ""
    script: str = ""
    creative_seed: Optional[int] = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str
    template_id: str
    creative_seed: int
    status: str
    dedup_passed: bool
    similarity_score: float
    error_message: str
    created_at: datetime
    updated_at: datetime


# ====== 平台版本 ======
class VariantResponse(BaseModel):
    id: str
    project_id: str
    platform: str
    file_path: str
    file_size: int
    width: int
    height: int
    duration: float
    cover_path: str
    title: str
    description: str
    tags: List[str]
    status: str
    review_notes: str


class VariantCreate(BaseModel):
    project_id: str
    platform: str
    title: str = ""
    description: str = ""
    tags: List[str] = []


# ====== 发布 ======
class PublishConfig(BaseModel):
    platform: str
    account_id: str = ""
    title: str = ""
    description: str = ""
    tags: List[str] = []
    scheduled_at: Optional[datetime] = None


class PublishRequest(BaseModel):
    project_id: str
    platforms: List[PublishConfig]


class PublishResponse(BaseModel):
    task_id: str
    status: str
    message: str


class DeliveryResponse(BaseModel):
    id: str
    variant_id: str
    platform: str
    account_id: str
    status: str
    error_message: str
    retry_count: int
    published_url: str
    scheduled_at: Optional[datetime]
    published_at: Optional[datetime]


# ====== 查重 ======
class DedupRequest(BaseModel):
    variant_id: str


class DedupResponse(BaseModel):
    variant_id: str
    decision: str  # pass/review/reject
    decision_score: float
    full_hash_distance: float
    segment_coverage: float
    visual_score: float
    audio_score: float
    similar_videos: List[Dict[str, Any]] = []
    details: Dict[str, Any] = {}


# ====== 相似度 ======
class SimilarityResultResponse(BaseModel):
    id: str
    source_id: str
    target_id: str
    full_hash_distance: float
    segment_coverage: float
    visual_score: float
    audio_score: float
    decision_score: float
    decision: str
    checked_at: datetime
