# 🦀 海鲜视频工坊 —— 创意混剪 · 自查重 · 一键多平台发布

把门店拍的海鲜素材，自动剪成**每条都不一样**的抖音 / 快手 / 视频号 / 小红书短视频。生成前后都与**自己的历史作品**做查重，再**稳定地**一键分发。

- 设计方案（含对 v1 元宝方案的评审）：[`docs/design.md`](docs/design.md)
- v1 原始代码已归档到 `legacy_v1/`（另有 `../seafood-video-systemtar.gz` 备份）

## 能力一览

| 模块 | 做法 |
|---|---|
| 素材入库 | 安全上传或服务器目录导入 → ffprobe 选择主视频流（排除 DJI 的 MJPEG 封面流）→ **一次性归一化代理**：旋转固化、HDR(HLG/PQ)→SDR（OpenCL hable 色调映射）、10bit→8bit、30fps、短边 1080 → PySceneDetect 镜头切分 → 逐帧特征（运动 / 清晰 / 鲜艳 / 暖色 / 曝光）→ 近似重复素材自动归入同一来源分组 |
| 创意编排 | 6 种叙事模板（钩子揭晓 / 快剪卡点 / 到港实拍 / 挑选攻略 / 今日福利 / 沉浸式），按平台**轮换**而非随机 · 选片时**新鲜度优先**（历史用过的片段按半衰期降权）· 重新构图（局部放大 + 平移）· 运镜 · 慢放 · 转场与硬切混排 · 整片统一调色 · 花字（钩子大标题 / 卖点字幕 / 角标 / CTA）· 文字自动排版不断词 · 文案库自动避开近期用过的钩子，过广告法极限词检查（可选 DeepSeek 大模型生成文案）|
| 渲染 | **主后端：剪映 me（本地 C++/Metal 引擎，MCP 调用）**，49 种剪映式转场、16 种花字，并保留 `.jmeproj` 工程供人工精修；**兜底后端：FFmpeg**（Linux / CI 可用）|
| 查重 | L1 **来源重合 P**（基于剪辑方案精确计算，渲染前就能把关，不合格自动换方案重编）· L2 **画面时序覆盖 V**（镜像不变 pHash + 多尺度裁剪 + 时序一致性校验，对镜像、调色、裁剪、0.8–1.25 倍变速、重编码鲁棒）· L3 **音频覆盖 A**（Philips 式音频指纹）→ 通过 / 复核 / 拒绝；素材用尽时明确提示“需要补拍” |
| 一键发布 | 全量预检（查重结论、文件完整性、规格、素材授权、广告法、令牌、配额、熔断）→ 每个成片×账号一条持久化状态机：断点续传、指数退避、令牌自动刷新、**创建结果不明时先对账再决定是否重提（不重复发布）**、异步审核轮询、平台级熔断 · 抖音、快手走开放接口 · 视频号、小红书走**人工辅助通道**（发布包 + 手机扫码交接页 + 回填链接确认）|
| 运维 | 数据库持久化任务队列（租约 / 心跳 / 去重键，不需要 Redis/Celery）· API Key 鉴权 · 媒体文件用签名 URL 访问 · 平台令牌 Fernet 加密存储 · 审计事件 · Vue3 管理台 |

## 快速开始（单机）

```bash
# 依赖：Python 3.11、FFmpeg（含 libass）、Node 18+；macOS 上可选剪映 me（自动探测 ../../jianying-me 或 /Applications）
./scripts/start.sh                 # → http://127.0.0.1:8010 ，API Key: dev-key-change-me
```

> 本机 8000 端口已被另一个项目（`~/code/ai-video`）占用，因此本系统默认使用 **8010**。

### 用 examples 素材跑完整流程

```bash
./scripts/run_demo.sh
```

依次执行：导入 `../examples` 下 17 段素材 → 分析 → 两轮生成（4 个平台 × 每平台 2 条）并查重 → 一键发布。发布时抖音、快手连接本地**模拟开放平台**（`backend/mock_platforms/server.py`，仅用于测试，并会注入“创建接口丢失响应”“上传 500”等故障）；视频号、小红书通过手机交接页确认。

## 实测结果（2026-10-23，Apple Silicon，本仓库 examples 素材）

| 项目 | 结果 |
|---|---|
| 素材 | 17 段（8 段 DJI 1728×3072 10bit 59.94fps，5 段 iPhone HLG HDR 带 -90° 旋转，4K 横拍等），94 秒内全部分析完成 |
| 生成 | 3 轮共 20 条成片（剪映 me 渲染约 9–12 秒/条，两路并行，约 50 秒/轮 8 条），查重全部通过；同平台历史作品之间最大来源重合 18%，库覆盖率随轮次上升到 35%（新鲜度信号真实可见）|
| 发布 | 抖音：创建接口响应丢失 → `create_unknown` → 对账找回作品 → 已发布；快手：上传两次 500 → 断点续传 → 已发布；视频号、小红书：交接页确认 → 已发布。**模拟平台上每个平台只有 1 个作品，无重复** |
| 测试 | `pytest`：34 项全部通过（指纹不变性 / 区分度、来源重合、编排、队列租约、10 种发布故障场景、API 安全）|

指纹在真实素材上的验证（τ=14，与 3 段不同素材对比，误报为 0）：镜像 / 调色 / 重编码 / 10% 裁剪的覆盖率为 1.00，变速 1.1 倍为 0.77，组合变换为 0.77，偏心放大 1.3 倍为 0.50（这种情况由 L1 来源层精确兜底）。

## 生产部署

```
API (Linux)      uvicorn app.main:app                       ← 可多副本
Worker (Linux)   python -m app.jobs.worker --queues analyze,dedup,publish
Render (macOS)   python -m app.jobs.worker --queues render  ← 剪映 me 渲染节点，可多台 Mac mini
DB               PostgreSQL（DATABASE_URL=postgresql+psycopg://…）；单机可用 SQLite（WAL）
Storage          DATA_DIR 放在共享存储（NAS / 挂载卷）
```

- `docker/docker-compose.yml`：PostgreSQL + API + Linux worker（FFmpeg 渲染）+ 管理台。
- 配置项见 `backend/.env.example`。`ENV=prod` 时，如果仍在使用默认密钥，服务会拒绝启动。

### 平台接入清单

1. **抖音**：在开放平台申请 `video.create.bind` 等权限（公开资料显示该能力对主体资质有限制；未获准入时把账号设为“人工辅助”模式即可），配置 `DOUYIN_CLIENT_KEY/SECRET`，在“平台账号”页点 OAuth 授权。
2. **快手**：申请 `user_video_publish`，配置 `KUAISHOU_APP_ID/SECRET`，然后 OAuth 授权。
3. **视频号 / 小红书**：没有公开的发布接口，账号添加为“人工辅助”。`PUBLIC_BASE_URL` 必须是运营手机能访问到的地址。
4. 上线前请对照开放平台控制台**核对接口路径**：上传、创建、发起上传、发布这几个接口已与 2026-10 官方文档核对；抖音分片上传、作品查询 / 列表，以及快手作品查询 / 列表的路径根据公开资料编写，未能联调，已全部做成可配置。

## 已知限制（如实说明）

- **无法保证通过平台查重**：平台查重是黑盒。本系统保证的是成片与**自己的历史作品**在来源、画面、声音上的差异可量化、可解释，并用比较保守的阈值把关。阈值需要用真实发布数据回归校准。
- 素材少时差异空间有限：17 段素材在每个平台上剪到二三十条以后，重合度会逐步升高，系统会转为“复核”或“素材不足”，提醒补拍，而不会靠加噪、镜像等手段蒙混。
- 剪映 me 只支持 macOS；Linux 渲染节点使用 FFmpeg 后端（转场和花字效果是近似实现）。
- 剪映 me 导出预设标称 -14 LUFS，实测约 -17.5 LUFS，建议在剪映 me 中复核；FFmpeg 后端实测 -14.9 LUFS。
- 字幕默认使用苹方字体，商用前请确认字体授权，或改用思源黑体、阿里巴巴普惠体等免费商用字体。
- 未实现视频号、小红书的网页自动化（RPA）发布：页面结构经常变化、难以稳定，而且有违反平台协议的风险。

## 目录

```
backend/
  app/
    config.py db.py models.py security.py main.py
    media/      ffmpeg.py(探测/归一化代理/读帧) analysis.py(镜头+特征) beats.py(BGM节拍)
    creative/   templates.py planner.py(EDL编排) copywriter.py(模板/LLM) textfit.py lint.py(广告法)
    render/     jianying.py(MCP客户端) ffmpeg_backend.py base.py
    dedup/      phash.py audio_fp.py matcher.py provenance.py engine.py
    publish/    base.py(错误分类/HTTP) douyin.py kuaishou.py registry.py orchestrator.py(预检/状态机/对账)
    jobs/       queue.py worker.py handlers.py
    services/   ingest.py generate.py
    api/        materials.py projects.py publish.py system.py serializers.py
  mock_platforms/server.py   # 仅测试用：抖音/快手开放接口模拟 + 故障注入
  tests/                     # pytest
  scripts/demo_e2e.py
frontend/                    # Vue3 + Element Plus 管理台
docker/  scripts/  docs/  legacy_v1/
```
