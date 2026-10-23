"""
系统全局配置
"""
import os
from pathlib import Path

# ====== 路径配置 ======
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"
TEMP_DIR = DATA_DIR / "temp"
DB_DIR = DATA_DIR / "db"

for d in [DATA_DIR, UPLOAD_DIR, OUTPUT_DIR, TEMP_DIR, DB_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ====== 数据库 ======
# 支持环境变量覆盖；默认使用 /tmp 以兼容特殊文件系统
import tempfile
_default_db_path = os.getenv("DATABASE_PATH", f"{tempfile.gettempdir()}/seafood_video.db")
DATABASE_URL = f"sqlite:///{_default_db_path}"

# ====== 指纹配置 ======
FINGERPRINT_FRAME_STEP = 1.0   # 每秒抽一帧
FINGERPRINT_HASH_SIZE = 16     # pHash 尺寸
SIMILARITY_FULL_HASH_THRESHOLD = 0.70   # 全片哈希阈值
SIMILARITY_SEGMENT_COVERAGE_THRESHOLD = 0.40  # 片段覆盖阈值
SIMILARITY_DECISION_THRESHOLD = 0.75    # 综合决策阈值

# ====== 视频规格 ======
PLATFORM_SPECS = {
    "douyin": {
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "bitrate": "5000k",
        "max_duration": 900,  # 15分钟
        "max_size_mb": 4096,
        "cover_ratio": "16:9",
    },
    "kuaishou": {
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "bitrate": "5000k",
        "max_duration": 180,  # 建议不超过3分钟
        "max_size_mb": 500,
    },
    "video_channel": {
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "bitrate": "10000k",
        "max_duration": 28800,  # 8小时
        "max_size_mb": 20480,
    },
    "xiaohongshu": {
        "width": 1080,
        "height": 1440,  # 小红书偏好3:4
        "fps": 30,
        "bitrate": "5000k",
        "max_duration": 300,  # 建议5分钟内
        "max_size_mb": 1024,
    },
}

# ====== 创意混剪配置 ======
CREATIVE_CONFIG = {
    "min_shots": 3,
    "max_shots": 15,
    "min_segment_duration": 1.5,
    "max_segment_duration": 8.0,
    "transition_types": ["fade", "wipeleft", "wiperight", "slideleft", "slideright", "circle", "dissolve"],
    "default_bgm_volume": 0.3,
    "voiceover_volume": 1.0,
}

# ====== 发布平台配置 ======
PLATFORM_CONFIG = {
    "douyin": {
        "has_api": True,
        "requires_user_auth": True,
        "supports_unattended": False,  # 需要人工确认
        "api_base": "https://open.douyin.com",
        "auth_url": "https://open.douyin.com/platform/oauth/connect/",
        "token_url": "https://open.douyin.com/oauth/access_token/",
        "upload_url": "/api/douyin/v1/video/upload_video/",
        "create_url": "/api/douyin/v1/video/create_video/",
        "scope": "video.create.bind",
    },
    "kuaishou": {
        "has_api": True,
        "requires_user_auth": True,
        "supports_unattended": True,
        "api_base": "https://open.kuaishou.com",
        "auth_url": "https://open.kuaishou.com/oauth2/authorize",
        "token_url": "https://open.kuaishou.com/oauth2/access_token",
        "upload_url": "https://open.kuaishou.com/openapi/photo/start_upload",
        "publish_url": "https://open.kuaishou.com/openapi/photo/publish",
        "scope": "user_info,photo_publish",
    },
    "video_channel": {
        "has_api": False,
        "requires_user_auth": True,
        "supports_unattended": False,
        "manual_url": "https://channels.weixin.qq.com/",
        "instructions": "请使用视频号助手网页端或手机端发布",
    },
    "xiaohongshu": {
        "has_api": False,
        "requires_user_auth": True,
        "supports_unattended": False,
        "manual_url": "https://creator.xiaohongshu.com/publish/publish",
        "instructions": "请使用小红书创作中心网页端发布",
    },
}

# ====== Celery ======
CELERY_BROKER_URL = "redis://localhost:6379/0"
CELERY_RESULT_BACKEND = "redis://localhost:6379/0"

# ====== 安全 ======
SECRET_KEY = os.getenv("SECRET_KEY", "seafood-video-system-secret-key-change-in-production")
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7天
