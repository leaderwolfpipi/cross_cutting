"""
FastAPI 主应用入口
"""
import sys
from pathlib import Path

# 确保项目根目录在 path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.core.database import init_db, get_db
from app.core.config import OUTPUT_DIR, UPLOAD_DIR, DATA_DIR
from app.routers import materials, projects, dedup, publish

app = FastAPI(
    title="海鲜视频混剪与多平台发布系统",
    description="创意视频混剪 + 自动查重 + 一键多平台发布",
    version="1.0.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由
app.include_router(materials.router, prefix="", tags=["素材"])
app.include_router(projects.router, prefix="", tags=["项目"])
app.include_router(dedup.router, prefix="", tags=["查重"])
app.include_router(publish.router, prefix="", tags=["发布"])

# 静态文件
DATA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/outputs", StaticFiles(directory=str(OUTPUT_DIR)), name="outputs")
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")


@app.on_event("startup")
async def startup_event():
    """应用启动时初始化数据库"""
    init_db()


@app.get("/")
async def root():
    """系统信息"""
    return {
        "name": "海鲜视频混剪与多平台发布系统",
        "version": "1.0.0",
        "status": "running",
        "modules": [
            {"name": "素材管理", "status": "active", "path": "/api/materials"},
            {"name": "创意混剪", "status": "active", "path": "/api/projects"},
            {"name": "查重检测", "status": "active", "path": "/api/dedup"},
            {"name": "多平台发布", "status": "active", "path": "/api/publish"},
        ],
        "platforms": [
            {"name": "抖音", "api": True, "auto": False, "note": "需要OAuth授权+人工确认"},
            {"name": "快手", "api": True, "auto": True, "note": "完整API支持"},
            {"name": "视频号", "api": False, "auto": False, "note": "半自动发布"},
            {"name": "小红书", "api": False, "auto": False, "note": "半自动发布"},
        ],
    }


@app.get("/health")
async def health_check():
    """健康检查"""
    from app.core.database import SessionLocal
    try:
        from sqlalchemy import text
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        return {"status": "unhealthy", "database": "disconnected", "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
