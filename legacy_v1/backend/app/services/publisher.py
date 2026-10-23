"""
多平台发布适配器
抖音/快手: API 发布
视频号/小红书: 半自动（生成发布任务 + 人工确认）
"""
import json
import time
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
from urllib.parse import urlencode
import base64

from app.core.config import PLATFORM_CONFIG, PLATFORM_SPECS
from app.models.database import PlatformAccount, Delivery, Variant


class PlatformPublisher:
    """平台发布基类"""

    def __init__(self, db_session=None):
        self.db = db_session

    def publish(self, variant: Variant, account: PlatformAccount,
                title: str = "", description: str = "", tags: List[str] = [],
                scheduled_at=None) -> Dict[str, Any]:
        """发布入口"""
        raise NotImplementedError


class DouyinPublisher(PlatformPublisher):
    """抖音发布适配器
    注意：抖音API需要用户授权，且公开准入受限。
    这里实现标准的OAuth 2.0授权码流程 + 视频上传/创建。
    """

    def __init__(self, db_session=None):
        super().__init__(db_session)
        self.config = PLATFORM_CONFIG["douyin"]

    def get_auth_url(self, client_key: str, redirect_uri: str, state: str = "") -> str:
        """获取授权URL"""
        params = {
            "client_key": client_key,
            "response_type": "code",
            "scope": self.config["scope"],
            "redirect_uri": redirect_uri,
            "state": state,
        }
        return f"{self.config['auth_url']}?{urlencode(params)}"

    def exchange_token(self, client_key: str, client_secret: str,
                       code: str, redirect_uri: str) -> Dict[str, Any]:
        """用授权码换取 access_token"""
        # 实际实现需要调用抖音 token 接口
        # POST https://open.douyin.com/oauth/access_token/
        # 这里返回模拟结果
        return {
            "access_token": "MOCK_ACCESS_TOKEN",
            "expires_in": 7200,
            "refresh_token": "MOCK_REFRESH_TOKEN",
            "open_id": "MOCK_OPEN_ID",
        }

    def upload_video(self, access_token: str, video_path: str) -> str:
        """
        上传视频到抖音
        返回 video_id
        """
        # 实际流程：
        # 1. POST /api/douyin/v1/video/upload_video/ 获取 upload_url
        # 2. 分片上传到 upload_url
        # 3. 返回 video_id
        # 这里简化为模拟返回
        return f"douyin_video_{int(time.time())}"

    def create_video(self, access_token: str, open_id: str, video_id: str,
                    text: str, cover_url: str = "", scheduled_time: int = 0) -> Dict:
        """
        创建视频（发布到抖音）
        POST /api/douyin/v1/video/create_video/
        """
        # 实际请求体：
        # {
        #   "open_id": "USER_OPEN_ID",
        #   "video_id": "VIDEO_ID",
        #   "text": "视频描述 #话题",
        #   "cover_url": "封面URL",
        #   "schedule_time": 0  # 0=立即发布
        # }
        # 这里返回模拟结果
        return {
            "data": {
                "item_id": f"douyin_item_{int(time.time())}",
                "share_url": "https://www.douyin.com/video/MOCK_ID",
            },
            "error_code": 0,
            "description": "success",
        }

    def publish(self, variant: Variant, account: PlatformAccount,
                title: str = "", description: str = "", tags: List[str] = [],
                scheduled_at=None) -> Dict[str, Any]:
        """完整发布流程"""
        try:
            # 检查授权
            if not account.access_token:
                return {
                    "success": False,
                    "error": "账号未授权，请先完成 OAuth 授权",
                    "requires_auth": True,
                    "auth_url": self.get_auth_url("YOUR_CLIENT_KEY", "YOUR_REDIRECT_URI"),
                }

            # 拼接话题标签
            if tags:
                tag_str = " ".join(f"#{t}" for t in tags)
                text = f"{title}\n{description}\n{tag_str}"
            else:
                text = f"{title}\n{description}"

            # 1. 上传视频
            video_id = self.upload_video(account.access_token, variant.file_path)

            # 2. 创建视频（发布）
            schedule_time = int(scheduled_at.timestamp()) if scheduled_at else 0
            result = self.create_video(
                access_token=account.access_token,
                open_id=account.open_id,
                video_id=video_id,
                text=text,
                scheduled_time=schedule_time,
            )

            if result.get("error_code") == 0:
                return {
                    "success": True,
                    "platform_video_id": result["data"].get("item_id", ""),
                    "share_url": result["data"].get("share_url", ""),
                    "status": "auditing",  # 抖音审核中
                    "message": "视频已提交，等待平台审核",
                }
            else:
                return {
                    "success": False,
                    "error": result.get("description", "发布失败"),
                    "error_code": result.get("error_code"),
                }

        except Exception as e:
            return {"success": False, "error": str(e)}


class KuaishouPublisher(PlatformPublisher):
    """快手发布适配器
    快手有完整的服务端发布API：
    1. 发起上传获取 upload_token
    2. 上传视频文件
    3. 发布视频（异步）
    4. 查询发布状态
    """

    def __init__(self, db_session=None):
        super().__init__(db_session)
        self.config = PLATFORM_CONFIG["kuaishou"]

    def get_auth_url(self, app_id: str, redirect_uri: str, state: str = "") -> str:
        """获取快手授权URL"""
        params = {
            "app_id": app_id,
            "response_type": "code",
            "scope": self.config["scope"],
            "redirect_uri": redirect_uri,
            "state": state,
        }
        return f"{self.config['auth_url']}?{urlencode(params)}"

    def exchange_token(self, app_id: str, app_secret: str,
                       code: str, redirect_uri: str) -> Dict[str, Any]:
        """换取 access_token"""
        return {
            "access_token": "MOCK_KS_TOKEN",
            "expires_in": 86400 * 30,
            "refresh_token": "MOCK_KS_REFRESH",
            "open_id": "MOCK_KS_OPEN_ID",
        }

    def start_upload(self, access_token: str, app_id: str) -> str:
        """发起上传，获取 upload_token"""
        # POST https://open.kuaishou.com/openapi/photo/start_upload
        return f"ks_upload_token_{int(time.time())}"

    def upload_file(self, upload_token: str, video_path: str,
                    access_token: str, app_id: str) -> bool:
        """上传视频文件（支持分片）"""
        file_size = Path(video_path).stat().st_size
        # 快手建议：小于10MB直接上传，大于10MB分片上传
        # 这里模拟上传
        return True

    def publish_video(self, access_token: str, app_id: str, upload_token: str,
                     caption: str, topics: List[str] = None) -> Dict:
        """
        发布视频
        POST https://open.kuaishou.com/openapi/photo/publish
        注意：异步发布，接口返回成功不代表已发布
        """
        # 实际请求体：
        # {
        #   "app_id": "APP_ID",
        #   "upload_token": "TOKEN",
        #   "caption": "视频描述 #话题",
        #   "topics": ["海鲜", "美食"]
        # }
        return {
            "photo_id": f"ks_photo_{int(time.time())}",
            "status": "processing",
        }

    def query_photo_status(self, access_token: str, photo_id: str) -> Dict:
        """查询视频发布状态"""
        # POST https://open.kuaishou.com/openapi/photo/status
        return {
            "status": "published",
            "play_url": "https://www.kuaishou.com/short-video/MOCK_ID",
        }

    def publish(self, variant: Variant, account: PlatformAccount,
                title: str = "", description: str = "", tags: List[str] = [],
                scheduled_at=None) -> Dict[str, Any]:
        """完整发布流程"""
        try:
            if not account.access_token:
                return {
                    "success": False,
                    "error": "账号未授权",
                    "requires_auth": True,
                    "auth_url": self.get_auth_url("YOUR_APP_ID", "YOUR_REDIRECT_URI"),
                }

            caption = title
            if description:
                caption += f"\n{description}"
            if tags:
                caption += " " + " ".join(f"#{t}" for t in tags)

            # 1. 发起上传
            upload_token = self.start_upload(account.access_token, "YOUR_APP_ID")

            # 2. 上传文件
            uploaded = self.upload_file(upload_token, variant.file_path,
                                        account.access_token, "YOUR_APP_ID")
            if not uploaded:
                return {"success": False, "error": "视频上传失败"}

            # 3. 发布
            result = self.publish_video(
                access_token=account.access_token,
                app_id="YOUR_APP_ID",
                upload_token=upload_token,
                caption=caption,
                topics=tags,
            )

            if result.get("photo_id"):
                # 4. 查询最终状态（异步，这里简化为立即查询）
                time.sleep(2)  # 实际应该用任务队列异步查询
                status = self.query_photo_status(account.access_token, result["photo_id"])

                if status.get("status") == "published":
                    return {
                        "success": True,
                        "platform_video_id": result["photo_id"],
                        "share_url": status.get("play_url", ""),
                        "status": "published",
                        "message": "视频已成功发布到快手",
                    }
                else:
                    return {
                        "success": True,
                        "platform_video_id": result["photo_id"],
                        "status": "processing",
                        "message": "视频处理中，稍后可查看",
                    }
            else:
                return {"success": False, "error": "发布失败"}

        except Exception as e:
            return {"success": False, "error": str(e)}


class VideoChannelPublisher(PlatformPublisher):
    """微信视频号发布适配器
    视频号没有公开的服务器端点赞/发布API。
    策略：生成适配好的视频文件 + 发布指引，通过视频号助手人工发布。
    """

    def __init__(self, db_session=None):
        super().__init__(db_session)
        self.config = PLATFORM_CONFIG["video_channel"]

    def prepare_publish_task(self, variant: Variant, account: PlatformAccount,
                             title: str = "", description: str = "",
                             tags: List[str] = []) -> Dict[str, Any]:
        """准备半自动发布任务"""
        return {
            "success": True,
            "requires_manual": True,
            "platform": "video_channel",
            "manual_url": self.config["manual_url"],
            "instructions": [
                "1. 打开视频号助手网页端",
                "2. 点击'发布视频'",
                f"3. 上传文件: {variant.file_path}",
                f"4. 设置封面: {variant.cover_path}",
                f"5. 填写标题: {title}",
                f"6. 填写描述: {description}",
                f"7. 添加话题: {' '.join('#' + t for t in tags)}",
                "8. 点击发布",
            ],
            "video_path": variant.file_path,
            "cover_path": variant.cover_path,
            "title": title,
            "description": description,
            "tags": tags,
            "specs": PLATFORM_SPECS["video_channel"],
        }

    def publish(self, variant: Variant, account: PlatformAccount,
                title: str = "", description: str = "", tags: List[str] = [],
                scheduled_at=None) -> Dict[str, Any]:
        """视频号不支持服务端自动发布，返回半自动任务"""
        return self.prepare_publish_task(variant, account, title, description, tags)


class XiaohongshuPublisher(PlatformPublisher):
    """小红书发布适配器
    小红书没有公开的笔记发布API。
    策略：生成适配好的视频 + 发布内容草稿，通过创作中心人工发布。
    """

    def __init__(self, db_session=None):
        super().__init__(db_session)
        self.config = PLATFORM_CONFIG["xiaohongshu"]

    def prepare_publish_task(self, variant: Variant, account: PlatformAccount,
                             title: str = "", description: str = "",
                             tags: List[str] = []) -> Dict[str, Any]:
        """准备半自动发布任务"""
        return {
            "success": True,
            "requires_manual": True,
            "platform": "xiaohongshu",
            "manual_url": self.config["manual_url"],
            "instructions": [
                "1. 打开小红书创作中心网页端",
                "2. 点击'发布笔记' -> '上传视频'",
                f"3. 上传文件: {variant.file_path}",
                f"4. 选择封面: {variant.cover_path}",
                f"5. 填写标题: {title}（小红书标题建议20字以内）",
                f"6. 填写正文: {description}",
                f"7. 添加话题标签: {' '.join('#' + t for t in tags)}",
                "8. 点击'发布'按钮",
            ],
            "video_path": variant.file_path,
            "cover_path": variant.cover_path,
            "title": title,
            "description": description,
            "tags": tags,
            "specs": PLATFORM_SPECS["xiaohongshu"],
        }

    def publish(self, variant: Variant, account: PlatformAccount,
                title: str = "", description: str = "", tags: List[str] = [],
                scheduled_at=None) -> Dict[str, Any]:
        """小红书不支持服务端自动发布，返回半自动任务"""
        return self.prepare_publish_task(variant, account, title, description, tags)


# ====== 发布工厂 ======
PUBLISHERS = {
    "douyin": DouyinPublisher,
    "kuaishou": KuaishouPublisher,
    "video_channel": VideoChannelPublisher,
    "xiaohongshu": XiaohongshuPublisher,
}


def get_publisher(platform: str, db_session=None) -> PlatformPublisher:
    """获取对应平台的发布器"""
    publisher_class = PUBLISHERS.get(platform)
    if not publisher_class:
        raise ValueError(f"Unsupported platform: {platform}")
    return publisher_class(db_session)


def publish_to_all(variants_and_configs: List[Dict], db_session=None) -> List[Dict]:
    """
    批量发布到多平台
    variants_and_configs: [
        {
            "variant": Variant,
            "account": PlatformAccount,
            "title": str,
            "description": str,
            "tags": [str],
            "scheduled_at": datetime
        }
    ]
    """
    results = []

    for item in variants_and_configs:
        variant = item["variant"]
        platform = variant.platform
        account = item.get("account")

        try:
            publisher = get_publisher(platform, db_session)
            result = publisher.publish(
                variant=variant,
                account=account,
                title=item.get("title", ""),
                description=item.get("description", ""),
                tags=item.get("tags", []),
                scheduled_at=item.get("scheduled_at"),
            )
            results.append({
                "platform": platform,
                "variant_id": variant.id,
                "result": result,
            })
        except Exception as e:
            results.append({
                "platform": platform,
                "variant_id": variant.id,
                "result": {"success": False, "error": str(e)},
            })

    return results
