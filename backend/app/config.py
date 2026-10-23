"""Runtime configuration. Everything is overridable through environment variables or backend/.env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


PLATFORM_SPECS: dict[str, dict] = {
    # width/height = render canvas; limits = what the platform accepts (checked in publish preflight)
    "douyin": {"name": "抖音", "width": 1080, "height": 1920, "fps": 30, "target_duration": (18.0, 32.0),
               "max_duration": 900, "min_duration": 3, "max_size_mb": 4096, "title_max": 55, "text_max": 1000,
               "tags_max": 5, "mode": "api"},
    "kuaishou": {"name": "快手", "width": 1080, "height": 1920, "fps": 30, "target_duration": (15.0, 30.0),
                 "max_duration": 600, "min_duration": 3, "max_size_mb": 4096, "title_max": 55, "text_max": 500,
                 "tags_max": 5, "mode": "api"},
    "channels": {"name": "视频号", "width": 1080, "height": 1920, "fps": 30, "target_duration": (20.0, 40.0),
                 "max_duration": 3600, "min_duration": 3, "max_size_mb": 20480, "title_max": 16, "text_max": 1000,
                 "tags_max": 5, "mode": "assisted"},
    "xiaohongshu": {"name": "小红书", "width": 1080, "height": 1440, "fps": 30, "target_duration": (18.0, 35.0),
                    "max_duration": 900, "min_duration": 3, "max_size_mb": 20480, "title_max": 20, "text_max": 1000,
                    "tags_max": 10, "mode": "assisted"},
}
PLATFORMS = tuple(PLATFORM_SPECS)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BACKEND_DIR / ".env"), env_file_encoding="utf-8", extra="ignore")

    # ------------------------------------------------------------------ core
    env: str = "dev"
    data_dir: Path = BACKEND_DIR / "data"
    database_url: str = ""  # default: sqlite file under data_dir
    api_keys: str = "dev-key-change-me"  # comma separated; sent as X-API-Key
    secret_key: str = "dev-secret-change-me-0123456789abcdef"  # HMAC for signed media URLs
    token_encryption_key: str = ""  # Fernet key (urlsafe base64, 32 bytes); derived from secret_key when empty
    public_base_url: str = "http://127.0.0.1:8010"  # used in signed links and the mobile hand-off page
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    max_upload_mb: int = 4096
    import_roots: str = ""  # comma separated server folders that /api/materials/import may read
    embedded_worker: bool = False  # run worker threads inside the API process (dev convenience)
    embedded_worker_queues: str = "analyze,render,dedup,publish"

    # ------------------------------------------------------------------ media
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    hwaccel: str = "auto"  # auto | videotoolbox | none
    proxy_short_side: int = 1080
    proxy_crf: int = 18
    analysis_fps: float = 2.0

    # ------------------------------------------------------------------ render
    render_backend: str = "auto"  # auto | jianying | ffmpeg
    jianying_mcp_cmd: str = ""  # e.g. /path/jianying-me/scripts/mcp.sh or /Applications/JianyingMe.app/Contents/MacOS/JianyingMe --mcp
    jianying_timeout_s: int = 1800
    max_plan_attempts: int = 8

    # ------------------------------------------------------------------ copywriting
    copywriter: str = "template"  # template | llm (the text model below)

    # ------------------------------------------------------------------ models (OpenAI-compatible, see app/llm.py)
    agent_model_provider: str = "deepseek"  # deepseek uses https://api.deepseek.com/v1 unless base_url is set
    agent_model_base_url: str = ""
    agent_model_name: str = ""
    agent_model_api_key: str = ""
    agent_vision_model_provider: str = "openai"
    agent_vision_model_base_url: str = ""
    agent_vision_model_name: str = ""
    agent_vision_model_api_key: str = ""

    # ------------------------------------------------------------------ dedup
    dedup_scope: str = "platform"  # platform | global
    dedup_hamming: int = 14  # validated on examples: 0 false positives vs other clips, recall on speed/crop/grade
    dedup_reject_content: float = 0.60
    dedup_reject_score: float = 0.70
    dedup_review_content: float = 0.35
    dedup_review_score: float = 0.45
    dedup_review_library: float = 0.85
    plan_max_pair_overlap: float = 0.35  # L1 gate before rendering (pairwise source reuse)
    freshness_half_life_days: float = 14.0

    # ------------------------------------------------------------------ publishing
    douyin_client_key: str = ""
    douyin_client_secret: str = ""
    douyin_api_base: str = "https://open.douyin.com"
    kuaishou_app_id: str = ""
    kuaishou_app_secret: str = ""
    kuaishou_api_base: str = "https://open.kuaishou.com"
    kuaishou_upload_scheme: str = "http"  # the upload gateway in start_upload's `endpoint` is plain http per docs
    oauth_redirect_base: str = "http://127.0.0.1:8010"
    publish_http_timeout_s: float = 60.0
    publish_max_attempts: int = 6
    publish_poll_interval_s: int = 60
    publish_poll_max_s: int = 6 * 3600
    publish_reconcile_grace_s: int = 90  # wait this long for a lost create to show up before re-submitting
    account_min_interval_s: int = 600
    account_daily_limit: int = 10
    circuit_failures: int = 5
    circuit_cooldown_s: int = 300
    chunk_size_mb: int = 8

    # ------------------------------------------------------------------ worker
    worker_lease_s: int = 120
    worker_poll_s: float = 1.0
    render_concurrency: int = 2

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{(self.data_dir / 'db' / 'seafood.db').as_posix()}"

    @property
    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

    @property
    def import_root_paths(self) -> list[Path]:
        return [Path(p).expanduser().resolve() for p in self.import_roots.split(",") if p.strip()]

    def path(self, *parts: str) -> Path:
        p = self.data_dir.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def rel(self, p: Path | str) -> str:
        """Storage path relative to data_dir (what the database keeps)."""
        return Path(p).resolve().relative_to(self.data_dir.resolve()).as_posix()

    def abs(self, rel: str) -> Path:
        p = (self.data_dir / rel).resolve()
        if not p.is_relative_to(self.data_dir.resolve()):
            raise ValueError(f"path escapes data dir: {rel}")
        return p


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir = Path(s.data_dir).expanduser().resolve()
    for sub in ("db", "materials", "proxies", "thumbs", "renders", "covers", "projects", "history", "bgm", "tmp"):
        (s.data_dir / sub).mkdir(parents=True, exist_ok=True)
    return s

