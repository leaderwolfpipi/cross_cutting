"""
素材管理 API
"""
import os
from pathlib import Path
from typing import List, Optional
import cv2

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from sqlalchemy.orm import Session
from datetime import datetime

from app.core.database import get_db
from app.models.database import Material, Shot
from app.schemas.models import MaterialCreate, MaterialResponse
from app.core.config import UPLOAD_DIR
from app.services.fingerprint import compute_sha256

router = APIRouter(prefix="/api/materials", tags=["素材管理"])


@router.post("/upload", response_model=MaterialResponse)
async def upload_material(
    file: UploadFile = File(...),
    source_type: str = "self_shot",
    description: str = "",
    tags: str = "",
    db: Session = Depends(get_db),
):
    """上传视频素材"""
    # 保存文件
    file_path = UPLOAD_DIR / file.filename
    content = await file.read()

    with open(file_path, "wb") as f:
        f.write(content)

    # 获取视频元数据
    cap = cv2.VideoCapture(str(file_path))
    if not cap.isOpened():
        raise HTTPException(status_code=400, detail="Invalid video file")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = frame_count / fps if fps > 0 else 0
    cap.release()

    # 计算 SHA-256
    sha256 = compute_sha256(str(file_path))

    # 检查是否已存在
    existing = db.query(Material).filter(Material.sha256 == sha256).first()
    if existing:
        # 删除重复文件
        os.remove(file_path)
        raise HTTPException(status_code=409, detail="素材已存在")

    # 创建素材记录
    material = Material(
        filename=file.filename,
        file_path=str(file_path),
        file_size=len(content),
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        codec="h264",  # TODO: 从 ffprobe 获取
        sha256=sha256,
        source_type=source_type,
        description=description,
        tags=[t.strip() for t in tags.split(",") if t.strip()],
    )
    db.add(material)
    db.commit()
    db.refresh(material)

    return MaterialResponse(
        id=material.id,
        filename=material.filename,
        file_path=material.file_path,
        file_size=material.file_size,
        duration=material.duration,
        width=material.width,
        height=material.height,
        fps=material.fps,
        codec=material.codec,
        sha256=material.sha256,
        source_type=material.source_type,
        status=material.status,
        tags=material.tags,
        description=material.description,
        shot_count=0,
    )


@router.post("/{material_id}/detect-shots")
async def detect_shots(material_id: str, db: Session = Depends(get_db)):
    """对素材进行镜头切分"""
    material = db.query(Material).filter(Material.id == material_id).first()
    if not material:
        raise HTTPException(status_code=404, detail="Material not found")

    from app.services.creative_edit import CreativeEditor
    editor = CreativeEditor(db)
    shots = editor.detect_shots(material.file_path, material_id)

    # 保存镜头
    for shot_data in shots:
        shot = Shot(
            material_id=material_id,
            start_time=shot_data["start_time"],
            end_time=shot_data["end_time"],
            duration=shot_data["duration"],
            motion_score=shot_data["motion_score"],
            quality_score=shot_data["quality_score"],
            scene_type=editor.classify_shot(shot_data, material.file_path),
        )
        db.add(shot)

    db.commit()

    return {"message": f"Detected {len(shots)} shots", "shots": shots}


@router.get("/", response_model=List[MaterialResponse])
async def list_materials(
    skip: int = 0, limit: int = 100,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """素材列表"""
    query = db.query(Material)
    if status:
        query = query.filter(Material.status == status)

    materials = query.offset(skip).limit(limit).all()

    result = []
    for m in materials:
        result.append(MaterialResponse(
            id=m.id,
            filename=m.filename,
            file_path=m.file_path,
            file_size=m.file_size,
            duration=m.duration,
            width=m.width,
            height=m.height,
            fps=m.fps,
            codec=m.codec,
            sha256=m.sha256,
            source_type=m.source_type,
            status=m.status,
            tags=m.tags or [],
            description=m.description or "",
            shot_count=len(m.shots),
        ))
    return result


@router.get("/{material_id}", response_model=MaterialResponse)
async def get_material(material_id: str, db: Session = Depends(get_db)):
    """获取素材详情"""
    material = db.query(Material).filter(Material.id == material_id).first()
    if not material:
        raise HTTPException(status_code=404, detail="Material not found")

    return MaterialResponse(
        id=material.id,
        filename=material.filename,
        file_path=material.file_path,
        file_size=material.file_size,
        duration=material.duration,
        width=material.width,
        height=material.height,
        fps=material.fps,
        codec=material.codec,
        sha256=material.sha256,
        source_type=material.source_type,
        status=material.status,
        tags=material.tags or [],
        description=material.description or "",
        shot_count=len(material.shots),
    )


@router.delete("/{material_id}")
async def delete_material(material_id: str, db: Session = Depends(get_db)):
    """删除素材"""
    material = db.query(Material).filter(Material.id == material_id).first()
    if not material:
        raise HTTPException(status_code=404, detail="Material not found")

    # 删除文件
    try:
        if os.path.exists(material.file_path):
            os.remove(material.file_path)
    except Exception:
        pass

    db.delete(material)
    db.commit()
    return {"message": "Material deleted"}


@router.post("/{material_id}/blacklist")
async def blacklist_material(material_id: str, db: Session = Depends(get_db)):
    """将素材加入黑名单"""
    material = db.query(Material).filter(Material.id == material_id).first()
    if not material:
        raise HTTPException(status_code=404, detail="Material not found")

    material.status = "blacklisted"
    db.commit()
    return {"message": "Material blacklisted"}
