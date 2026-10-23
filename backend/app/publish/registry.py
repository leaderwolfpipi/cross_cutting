"""Platform → adapter. 视频号 and 小红书 have no public publishing API: their deliveries go to the assisted
(operator hand-off) channel, as does any account configured with mode=assisted."""

from __future__ import annotations

from .base import Adapter
from .douyin import DouyinAdapter
from .kuaishou import KuaishouAdapter

API_ADAPTERS = {"douyin": DouyinAdapter, "kuaishou": KuaishouAdapter}
ASSISTED_URLS = {
    "channels": "https://channels.weixin.qq.com/platform/post/create",
    "xiaohongshu": "https://creator.xiaohongshu.com/publish/publish",
    "douyin": "https://creator.douyin.com/creator-micro/content/upload",
    "kuaishou": "https://cp.kuaishou.com/article/publish/video",
}


def adapter(platform: str) -> Adapter:
    cls = API_ADAPTERS.get(platform)
    if cls is None:
        raise KeyError(f"{platform} has no API adapter (assisted only)")
    return cls()


def supports_api(platform: str) -> bool:
    return platform in API_ADAPTERS
