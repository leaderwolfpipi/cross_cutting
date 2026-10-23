"""
平台账号与发布管理 API
"""
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.database import get_db
from app.models.database import PlatformAccount, Delivery, PublishTask, Variant
from app.services.publisher import get_publisher

router = APIRouter(prefix="/api/publish", tags=["发布管理"])


class AccountCreate(BaseModel):
    platform: str
    account_name: str = ""
    account_id: str = ""
    access_token: str = ""
    refresh_token: str = ""
    open_id: str = ""
    scope: str = ""


class AccountResponse(BaseModel):
    id: str
    platform: str
    account_name: str
    account_id: str
    status: str
    token_expires_at: Optional[datetime]
    created_at: datetime


@router.post("/accounts", response_model=AccountResponse)
async def create_account(account: AccountCreate, db: Session = Depends(get_db)):
    """添加平台账号"""
    db_account = PlatformAccount(
        platform=account.platform,
        account_name=account.account_name,
        account_id=account.account_id,
        access_token=account.access_token,
        refresh_token=account.refresh_token,
        open_id=account.open_id,
        scope=account.scope,
    )
    db.add(db_account)
    db.commit()
    db.refresh(db_account)

    return AccountResponse(
        id=db_account.id,
        platform=db_account.platform,
        account_name=db_account.account_name,
        account_id=db_account.account_id,
        status=db_account.status,
        token_expires_at=db_account.token_expires_at,
        created_at=db_account.created_at,
    )


@router.get("/accounts", response_model=List[AccountResponse])
async def list_accounts(
    platform: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """获取账号列表"""
    query = db.query(PlatformAccount)
    if platform:
        query = query.filter(PlatformAccount.platform == platform)

    accounts = query.all()
    return [
        AccountResponse(
            id=a.id,
            platform=a.platform,
            account_name=a.account_name,
            account_id=a.account_id,
            status=a.status,
            token_expires_at=a.token_expires_at,
            created_at=a.created_at,
        )
        for a in accounts
    ]


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str, db: Session = Depends(get_db)):
    """删除账号"""
    account = db.query(PlatformAccount).filter(PlatformAccount.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    db.delete(account)
    db.commit()
    return {"message": "Account deleted"}


@router.get("/deliveries")
async def list_deliveries(
    skip: int = 0, limit: int = 100,
    platform: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """发布记录列表"""
    query = db.query(Delivery)
    if platform:
        query = query.filter(Delivery.platform == platform)
    if status:
        query = query.filter(Delivery.status == status)

    deliveries = query.order_by(Delivery.created_at.desc()).offset(skip).limit(limit).all()

    return [
        {
            "id": d.id,
            "variant_id": d.variant_id,
            "platform": d.platform,
            "account_id": d.account_id,
            "status": d.status,
            "error_message": d.error_message,
            "retry_count": d.retry_count,
            "published_url": d.published_url,
            "scheduled_at": d.scheduled_at,
            "published_at": d.published_at,
            "created_at": d.created_at,
        }
        for d in deliveries
    ]


@router.get("/tasks")
async def list_publish_tasks(
    skip: int = 0, limit: int = 50,
    db: Session = Depends(get_db),
):
    """发布任务列表"""
    tasks = db.query(PublishTask).order_by(
        PublishTask.created_at.desc()
    ).offset(skip).limit(limit).all()

    return [
        {
            "id": t.id,
            "project_id": t.project_id,
            "status": t.status,
            "progress": t.progress,
            "total_platforms": t.total_platforms,
            "completed_platforms": t.completed_platforms,
            "results": t.results,
            "error_message": t.error_message,
            "created_at": t.created_at,
            "completed_at": t.completed_at,
        }
        for t in tasks
    ]


@router.get("/platform-capabilities")
async def get_platform_capabilities():
    """获取各平台发布能力"""
    from app.core.config import PLATFORM_CONFIG, PLATFORM_SPECS

    capabilities = {}
    for platform, config in PLATFORM_CONFIG.items():
        capabilities[platform] = {
            "has_api": config["has_api"],
            "requires_user_auth": config["requires_user_auth"],
            "supports_unattended": config["supports_unattended"],
            "manual_instructions": config.get("instructions", ""),
            "manual_url": config.get("manual_url", ""),
            "specs": PLATFORM_SPECS.get(platform, {}),
        }

    return capabilities


@router.get("/manual-guide/{platform}")
async def get_manual_guide(platform: str, variant_id: str, db: Session = Depends(get_db)):
    """获取半自动发布指引（视频号/小红书）"""
    variant = db.query(Variant).filter(Variant.id == variant_id).first()
    if not variant:
        raise HTTPException(status_code=404, detail="Variant not found")

    publisher = get_publisher(platform, db)
    account = db.query(PlatformAccount).filter(
        PlatformAccount.platform == platform
    ).first()

    if hasattr(publisher, 'prepare_publish_task'):
        return publisher.prepare_publish_task(
            variant, account,
            title=variant.title,
            description=variant.description,
            tags=variant.tags,
        )

    return {"error": "Platform does not support manual guide"}
