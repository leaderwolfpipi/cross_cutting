"""
项目管理 API - 创意混剪编排
"""
import os
import json
import random
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from datetime import datetime

from app.core.database import get_db
from app.models.database import Project, Variant, Fingerprint, Material, Shot
from app.schemas.models import (
    ProjectCreate, ProjectResponse, VariantCreate, VariantResponse,
    PublishRequest, PublishResponse,
)
from app.services.creative_edit import CreativeEditor
from app.services.dedup import DedupEngine
from app.services.publisher import get_publisher, publish_to_all
from app.services.fingerprint import compute_sha256
from app.core.config import OUTPUT_DIR, PLATFORM_SPECS

router = APIRouter(prefix="/api/projects", tags=["项目管理"])


@router.post("/", response_model=ProjectResponse)
async def create_project(project_data: ProjectCreate, db: Session = Depends(get_db)):
    """创建剪辑项目"""
    import uuid

    project = Project(
        name=project_data.name,
        description=project_data.description,
        template_id=project_data.template_id,
        creative_seed=project_data.creative_seed or random.randint(1, 99999),
        selected_material_ids=project_data.selected_material_ids,
        selected_shot_ids=project_data.selected_shot_ids,
        bgm_id=project_data.bgm_id,
        script=project_data.script,
        status="draft",
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        template_id=project.template_id,
        creative_seed=project.creative_seed,
        status=project.status,
        dedup_passed=project.dedup_passed,
        similarity_score=project.similarity_score,
        error_message=project.error_message,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.post("/{project_id}/edit")
async def start_creative_edit(
    project_id: str,
    platforms: List[str] = ["douyin", "kuaishou", "video_channel", "xiaohongshu"],
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
):
    """
    启动创意混剪
    为指定项目生成各平台的混剪版本
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    project.status = "processing"
    db.commit()

    # 后台执行混剪
    background_tasks.add_task(execute_edit_task, project_id, platforms)

    return {"message": "混剪任务已启动", "project_id": project_id}


def execute_edit_task(project_id: str, platforms: List[str]):
    """后台执行混剪任务"""
    from app.core.database import SessionLocal
    db = SessionLocal()

    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return

        editor = CreativeEditor(db)

        for platform in platforms:
            try:
                result = editor.execute_edit(project, platform)

                if not result.get("success"):
                    project.error_message = result.get("error", "混剪失败")
                    continue

                # 保存 variant
                variant = Variant(
                    project_id=project.id,
                    platform=platform,
                    file_path=result["output_path"],
                    file_size=Path(result["output_path"]).stat().st_size if Path(result["output_path"]).exists() else 0,
                    width=PLATFORM_SPECS[platform]["width"],
                    height=PLATFORM_SPECS[platform]["height"],
                    duration=result.get("duration", 0),
                    cover_path=result.get("cover_path", ""),
                    title="",
                    description="",
                    tags=[],
                    sha256=result.get("sha256", ""),
                    status="ready",
                )
                db.add(variant)
                db.flush()

                # 保存指纹
                from app.services.dedup import DedupEngine
                dedup = DedupEngine(db)
                fp_data = dedup.build_fingerprint(result["output_path"], variant.id, result.get("segments"))
                dedup.save_fingerprint(fp_data)

            except Exception as e:
                project.error_message = f"Platform {platform} edit failed: {str(e)}"
                db.commit()

        project.status = "ready"
        db.commit()

    finally:
        db.close()


@router.post("/{project_id}/dedup")
async def run_dedup(project_id: str, db: Session = Depends(get_db)):
    """对项目所有变体做查重检测"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    variants = db.query(Variant).filter(Variant.project_id == project_id).all()
    if not variants:
        raise HTTPException(status_code=400, detail="No variants found")

    dedup = DedupEngine(db)
    results = []

    for variant in variants:
        try:
            result = dedup.check_variant(variant.id)
            results.append({
                "variant_id": variant.id,
                "platform": variant.platform,
                **result,
            })
        except Exception as e:
            results.append({
                "variant_id": variant.id,
                "platform": variant.platform,
                "decision": "error",
                "error": str(e),
            })

    # 更新项目状态
    all_pass = all(r["decision"] == "pass" for r in results)
    project.dedup_passed = all_pass
    if results:
        project.similarity_score = max(r.get("decision_score", 0) for r in results)
    db.commit()

    return {
        "project_id": project_id,
        "dedup_passed": all_pass,
        "results": results,
    }


@router.get("/{project_id}/variants", response_model=List[VariantResponse])
async def list_variants(project_id: str, db: Session = Depends(get_db)):
    """获取项目的所有平台版本"""
    variants = db.query(Variant).filter(Variant.project_id == project_id).all()

    result = []
    for v in variants:
        result.append(VariantResponse(
            id=v.id,
            project_id=v.project_id,
            platform=v.platform,
            file_path=v.file_path,
            file_size=v.file_size,
            width=v.width,
            height=v.height,
            duration=v.duration,
            cover_path=v.cover_path,
            title=v.title,
            description=v.description,
            tags=v.tags or [],
            status=v.status,
            review_notes=v.review_notes or "",
        ))
    return result


@router.put("/variants/{variant_id}")
async def update_variant(
    variant_id: str,
    title: str = "",
    description: str = "",
    tags: List[str] = [],
    status: str = "",
    review_notes: str = "",
    db: Session = Depends(get_db),
):
    """更新平台版本信息（标题、描述、标签、审核状态）"""
    variant = db.query(Variant).filter(Variant.id == variant_id).first()
    if not variant:
        raise HTTPException(status_code=404, detail="Variant not found")

    if title:
        variant.title = title
    if description:
        variant.description = description
    if tags:
        variant.tags = tags
    if status:
        variant.status = status
    if review_notes:
        variant.review_notes = review_notes

    db.commit()
    return {"message": "Variant updated"}


@router.post("/variants/{variant_id}/approve")
async def approve_variant(variant_id: str, db: Session = Depends(get_db)):
    """审核通过"""
    variant = db.query(Variant).filter(Variant.id == variant_id).first()
    if not variant:
        raise HTTPException(status_code=404, detail="Variant not found")

    variant.status = "approved"
    db.commit()
    return {"message": "Variant approved"}


@router.post("/variants/{variant_id}/reject")
async def reject_variant(variant_id: str, reason: str = "", db: Session = Depends(get_db)):
    """审核拒绝"""
    variant = db.query(Variant).filter(Variant.id == variant_id).first()
    if not variant:
        raise HTTPException(status_code=404, detail="Variant not found")

    variant.status = "rejected"
    variant.review_notes = reason
    db.commit()
    return {"message": "Variant rejected"}


@router.post("/publish", response_model=PublishResponse)
async def publish_project(request: PublishRequest, db: Session = Depends(get_db)):
    """
    一键发布到多平台
    """
    from app.models.database import PublishTask, Delivery

    # 创建发布任务
    task = PublishTask(
        project_id=request.project_id,
        platform_configs=[pc.dict() for pc in request.platforms],
        status="running",
        total_platforms=len(request.platforms),
        completed_platforms=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    # 为每个平台配置创建发布任务
    results = []
    for pc in request.platforms:
        # 获取对应的 variant
        variant = db.query(Variant).filter(
            Variant.project_id == request.project_id,
            Variant.platform == pc.platform,
        ).first()

        if not variant:
            results.append({
                "platform": pc.platform,
                "success": False,
                "error": "Variant not found",
            })
            continue

        if variant.status not in ("approved", "ready"):
            results.append({
                "platform": pc.platform,
                "success": False,
                "error": f"Variant status is {variant.status}, needs approval",
            })
            continue

        # 获取账号
        account = db.query(db.query.type if False else __import__("app").models.database.PlatformAccount).filter(
            __import__("app").models.database.PlatformAccount.platform == pc.platform
        ).first()

        # 执行发布
        try:
            publisher = get_publisher(pc.platform, db)
            result = publisher.publish(
                variant=variant,
                account=account,
                title=pc.title or variant.title,
                description=pc.description or variant.description,
                tags=pc.tags or variant.tags,
                scheduled_at=pc.scheduled_at,
            )

            # 保存 delivery 记录
            delivery = Delivery(
                variant_id=variant.id,
                platform=pc.platform,
                account_id=pc.account_id,
                platform_video_id=result.get("platform_video_id", ""),
                status="published" if result.get("success") else "failed",
                error_message="" if result.get("success") else result.get("error", ""),
                published_url=result.get("share_url", ""),
                published_at=datetime.utcnow() if result.get("success") else None,
            )
            db.add(delivery)
            results.append({
                "platform": pc.platform,
                "success": result.get("success", False),
                **result,
            })

        except Exception as e:
            results.append({
                "platform": pc.platform,
                "success": False,
                "error": str(e),
            })

    # 更新任务状态
    task.status = "completed"
    task.progress = 100
    task.completed_platforms = sum(1 for r in results if r.get("success"))
    task.results = results
    task.completed_at = datetime.utcnow()
    db.commit()

    return PublishResponse(
        task_id=task.id,
        status=task.status,
        message=f"发布完成: {task.completed_platforms}/{task.total_platforms} 平台成功",
    )


@router.get("/", response_model=List[ProjectResponse])
async def list_projects(
    skip: int = 0, limit: int = 50,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """项目列表"""
    query = db.query(Project)
    if status:
        query = query.filter(Project.status == status)

    projects = query.order_by(Project.created_at.desc()).offset(skip).limit(limit).all()

    result = []
    for p in projects:
        result.append(ProjectResponse(
            id=p.id,
            name=p.name,
            description=p.description,
            template_id=p.template_id,
            creative_seed=p.creative_seed,
            status=p.status,
            dedup_passed=p.dedup_passed,
            similarity_score=p.similarity_score,
            error_message=p.error_message,
            created_at=p.created_at,
            updated_at=p.updated_at,
        ))
    return result


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, db: Session = Depends(get_db)):
    """获取项目详情"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        template_id=project.template_id,
        creative_seed=project.creative_seed,
        status=project.status,
        dedup_passed=project.dedup_passed,
        similarity_score=project.similarity_score,
        error_message=project.error_message,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )
