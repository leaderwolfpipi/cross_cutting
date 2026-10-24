# 海鲜视频创意混剪 · 查重 · 一键多平台发布系统 —— 设计方案 v2

> v1（元宝方案）原文保留在仓库外层 `docs/design.md` 与 `seafood-video-systemtar.gz`。
> 本文先给出对 v1 的评审结论，再给出 v2 的完整设计；v2 即当前源码的实现依据。

---

## 0. 目标与边界（先把话说清楚）

| 需求原话 | v2 的工程化定义 |
|---|---|
| 混剪要有创意、吸眼球 | 每条成片有**钩子标题 + 叙事模板 + 节奏剪辑 + 花字/转场/调色 + 平台化文案**；成片可在剪映 me 中打开人工精修 |
| 同一素材剪出来也必须不重复 | 与**自己历史作品库**比对：素材来源重合度（精确）+ 画面时序覆盖（感知）+ 音频覆盖，三层判定；超过阈值不放行，自动换方案重剪 |
| 过平台查重 | 平台查重是黑盒，**无法保证**。系统能保证的是：成片与自己历史作品在“镜头来源、结构、文案、声音”上真实不同，并用比平台更保守的口径自检。**素材用尽时系统会明确提示“需要补拍”**，而不是靠加噪、镜像等手段蒙混 |
| 一键发布、必须稳定 | 一键 = **全量预检 → 批量建任务 → 每个平台独立的持久化状态机**；崩溃可恢复、重试不重复发布、结果可回查。无开放接口的平台（视频号、小红书）走“人工辅助通道”，系统把一切准备好，人点最后一下 |

**明确不做**：设备指纹伪造、多账号“模拟真人”反风控、Cookie/抓包/签名逆向发布、对成片注入不可见扰动来对抗指纹。这些手段违反平台协议，账号风险高，也与“稳定”目标相悖。

---

## 1. 对 v1（元宝方案）的评审

### 1.1 方案层面的问题

| # | v1 内容 | 问题 | v2 处理 |
|---|---|---|---|
| D1 | 全片 pHash 先粗筛，汉明距离大的直接跳过（附录 C：`phash_sim < 0.3` 跳过） | **混剪会重排镜头**，重排后的全片哈希与原片完全不同，粗筛会把最该拦下的视频放过去。这是查重设计的根本缺陷 | 取消全片哈希粗筛，改为**帧级哈希检索 + 时序对齐** |
| D2 | 指纹对镜像/调色/变速敏感（v1 把镜像、调色当成“差异化变换”） | 自检会给出“已不同”的虚假安全感，平台的深度拷贝检测（类 SSCD）对这些变换是鲁棒的 | 指纹做成**镜像不变**（左右对称化后再 DCT）、对亮度/色调鲁棒、对 0.8–1.25 倍变速做时序容忍；镜像、调色**不计入**差异化 |
| D3 | 只有渲染后才查重 | 浪费渲染算力，且无法解释“为什么重复” | 新增 **L1 来源层查重**：编排器输出剪辑决策表（EDL），渲染前就精确计算与历史作品的素材区间重合度，不合格直接换方案 |
| D4 | 跨平台一律互相查重 | 跨平台分发可以复用同一内容，但本项目当前目标是每次混剪都尽量有明显差异 | 默认全局比对自己的历史作品；确需跨平台复用时可设置 `DEDUP_SCOPE=platform` |
| D5 | 单一素材无限混剪 | 一个 10 秒素材剪 20 次必然高度重复，不可能“永远不重复” | 引入**素材新鲜度**：统计每段素材在历史中被用过多少次、何时用过，编排时优先选新鲜片段；整体新鲜度过低时提示补拍 |
| D6 | 10.4“反作弊策略”（不同设备指纹、避免同一网络、模拟真人节奏） | 属于对抗平台风控，违反平台协议，且与 10.2 自相矛盾 | 删除。改为**合规限流**：按账号的最小发布间隔、日上限、发布时间窗，目的是尊重接口配额和内容质量 |
| D7 | 小红书“Cookie”认证 | 同上 | 删除。视频号、小红书走人工辅助通道 |
| D8 | SQLite + Celery + 多 worker | SQLite 多进程写锁；Celery 的可见性超时会**重复投递长任务**，对“发布”这种非幂等操作是灾难 | 采用**数据库持久化任务队列**（租约 + 心跳 + 去重键），SQLite 开 WAL 用于单机，生产用 PostgreSQL（`SKIP LOCKED` 语义）；不再依赖 Redis/Celery |
| D9 | “失败重试时携带原 task_id，平台侧识别为同一任务” | 抖音、快手的创建接口**不支持客户端幂等键**，这个说法不成立；超时后盲目重试会重复发布 | 引入 **CREATE_UNKNOWN → 对账（reconcile）** 状态：创建请求结果不明时先查平台作品列表，按标记匹配，确认没发出去才重试 |
| D10 | 一键发布直接逐个上传 | 发到一半才发现某平台 token 过期、某视频超时长，结果“半发布” | 一键发布先做**全量预检**（授权、规格、查重结论、素材授权、广告法用词、配额），全部通过才开始；可选“跳过失败项” |
| D11 | 抖音按普通 OAuth 能力设计 | 据调研，抖音“代替用户发布”能力的准入有主体限制 | 抖音适配器按官方 OpenAPI 实现，同时允许账号配置为“人工辅助”模式；未获准入时自动降级 |
| D12 | 渲染引擎：FFmpeg 拼接 + xfade | 转场有限、文字效果弱、HDR 无法正确转换（实测本机 ffmpeg 无 zscale，HLG 素材直转发灰） | 渲染层抽象为后端接口；主后端选 **剪映 me（MCP）**，FFmpeg 为兜底后端（见 §3） |

### 1.2 代码层面的问题（v1 源码，均已在 v2 重写中修复）

- `compute_segment_coverage` **只比较时间戳，不比较哈希**：任意两个同等时长的视频覆盖率都是 1.0。
- 全片哈希是 `md5(拼接的帧哈希)`，对 md5 算汉明距离毫无意义；音频指纹也是 md5，只能判断“完全相同”。
- 帧哈希用字符串 Jaccard（要求完全相等），转码一次就失效。
- 渲染输出**没有音频轨**（只 `-map` 了视频）；计算好的转场从未使用（实际用的是 concat）；4 个平台用同一个 seed，内容几乎一样。
- `shell=True` 拼接命令，有注入风险，含空格/中文的路径会出错；封面使用了不存在的 `force_original_aspect_ratio=cover`。
- 字幕字体写死 `Microsoft YaHei`，macOS/Linux 上没有这个字体，中文会显示成方块。
- 上传接口使用 `UPLOAD_DIR / file.filename`，存在**路径穿越**；整个文件读入内存。
- 发布器全部返回**伪造的“成功”**（mock ID），这在生产中是最危险的行为。
- token 明文存储；API 无鉴权；数据库默认放在 `/tmp`（重启丢数据）；混剪用 `BackgroundTasks`，进程重启后任务丢失。
- 对实际素材不可用：DJI 文件含多路视频流（主码流 + MJPEG 封面）、10-bit HEVC、59.94fps；iPhone 文件是 HLG HDR 并带 -90° 旋转。v1 的 OpenCV 直读路径无法正确处理。

---

## 2. 总体架构

```
                       ┌──────────────────────── Vue3 管理台 ───────────────────────┐
                       │ 工作台 · 素材库 · 混剪项目/成片审核 · 查重中心 · 一键发布 · 账号 │
                       └──────────────────────────────┬─────────────────────────────┘
                                                      │ REST + X-API-Key；媒体走 HMAC 签名 URL
┌─────────────────────────────────────────────────────▼──────────────────────────────────────────┐
│ API 服务 (FastAPI，可部署在 Linux)                                                              │
│  素材/镜头 · 项目/成片 · 查重报告/复核 · 账号/OAuth · 一键发布/预检 · 人工发布任务 · 任务监控        │
└───────────────┬─────────────────────────────────────────────────────────────┬──────────────────┘
                │ 写入 jobs 表（持久化任务队列）                                  │
┌───────────────▼──────────────┐  ┌────────────────────────────┐  ┌────────────▼──────────────────┐
│ 分析 Worker (queue=analyze)   │  │ 渲染节点 (queue=render)     │  │ 发布 Worker (queue=publish)    │
│ ffprobe→归一化代理→镜头切分    │  │ 编排器(EDL)→L1来源查重→     │  │ 预检→上传(断点续传)→创建→     │
│ (PySceneDetect)→逐帧特征       │  │ 渲染后端→封面→L2/L3指纹查重  │  │ 对账→状态轮询；令牌刷新；熔断  │
│ →素材近似重复分组              │  │ 主：剪映me(MCP, macOS)       │  │ 抖音/快手 OpenAPI；视频号/小红书│
│                               │  │ 备：FFmpeg(任意平台)          │  │ 人工辅助通道                    │
└───────────────────────────────┘  └────────────────────────────┘  └───────────────────────────────┘
                │                               │                                  │
        ┌───────▼───────────────────────────────▼──────────────────────────────────▼──────┐
        │ PostgreSQL（单机可用 SQLite WAL） · 本地/NAS/对象存储（素材、代理、成片、封面、工程）   │
        └─────────────────────────────────────────────────────────────────────────────────┘
```

- **进程模型**：`api`（无状态，可多副本）+ `worker --queues ...`（可多机）。开发模式下可内嵌 worker 线程，一条命令启动。
- **渲染节点**：剪映 me 依赖 Metal/VideoToolbox/CoreText，只能运行在 macOS（Apple Silicon）上。生产建议 1～N 台 Mac mini 作为 render worker，从共享数据库领取任务；Linux 节点可以运行 FFmpeg 兜底后端。
- **存储**：所有路径都相对于 `DATA_DIR` 存储，便于 NAS 或对象存储挂载。

---

## 3. 渲染引擎选型（结论：剪映 me 为主，FFmpeg 兜底）

| 方案 | 结论 | 理由 |
|---|---|---|
| 官方剪映 + pyJianYingDraft 草稿 + 界面自动化导出 | ✗ | 剪映 6+ 草稿加密；导出靠模拟点击，无法后台运行、易卡死；有违反协议的风险 |
| 阿里云 IMS / 火山 / 腾讯云剪辑 API | 可选扩展 | 成熟可扩容，但按时长计费、素材需上云、查重是黑盒、绑定厂商 |
| Remotion / Revideo | ✗ | 4 人以上公司需付费授权；浏览器渲染 4K HEVC 素材慢 |
| MoviePy / editly / 纯 FFmpeg | 兜底 | 可移植，但转场和花字弱，本机 ffmpeg 无 zscale，HDR 处理差 |
| **剪映 me（本地 C++/Metal 引擎 + MCP）** | **主后端** | 实测：4 段 4K/HDR 素材合成 11.8 秒 1080×1920 成片，导出 6.4 秒；49 种剪映式 GPU 转场、16 种花字、滤镜、运镜、画布模糊、保持音调变速、HDR 色调映射、旋转校正；生成的 `.jmeproj` 可在 App 中人工精修 |

**集成方式**：渲染节点以子进程方式启动 `jianying-me` 的 MCP stdio 服务（开发树 `scripts/mcp.sh`，或 `JianyingMe.app --mcp`），一个渲染任务对应一个独立进程和工程。这样崩溃互不影响，也与其 Python 环境和版本解耦。

**后端无关的剪辑决策表（EDL v1）** 是编排器与渲染器之间的唯一契约：

```jsonc
{
  "version": 1,
  "canvas": {"width": 1080, "height": 1920, "fps": 30},
  "clips": [{
     "material_id": "…", "source_group": "…", "src_in": 2.0, "src_out": 4.6, "speed": 1.0,
     "out_duration": 2.6, "role": "hook",
     "transform": {"mode": "fill", "scale": 1.35, "x": 0.42, "y": 0.5},   // scale 1 = 适配画布（剪映语义）
     "background": "blur" | null,
     "animation": {"combo": "ken_burns"} | {"in": "zoom_in", "out": null},
     "color": {"filter": "鲜艳", "saturation": 1.08, "temperature": 0.1},
     "volume_db": -4, "transition_out": {"kind": "zoom_in", "duration": 0.4} | null
  }],
  "texts": [{"role": "hook", "text": "…", "start": 0, "duration": 2.6, "preset": "variety",
             "style": {"size": 0.062, "position_y": 0.2}, "anim_in": "pop", "anim_out": "fade"}],
  "audio": {"bgm": {"bgm_id": "…", "path": "…", "volume_db": -10} | null, "original_volume_db": -2},
  "cover": {"time": 1.2},
  "template_id": "hook_reveal", "seed": 12345
}
```

转场语义采用剪映规则：转场从前后两段各占一半，**总时长不变**。因此成片时长等于各 clip 的 `out_duration` 之和。

---

## 4. 模块一：素材入库与分析

1. **安全上传**：流式写盘，限制大小；存储名用 UUID，原名只作展示（杜绝路径穿越）；计算 SHA-256，相同文件拒绝重复入库。
2. **探测**：`ffprobe` 选主视频流（`0:v:0`，排除 attached_pic/MJPEG 封面），读取旋转、HDR 传递函数、帧率、音频。
3. **归一化代理（mezzanine）**：一次性转码为 SDR、8-bit、30fps、短边 1080、H.264 CRF18。HLG/PQ 素材优先用 `tonemap_opencl`（hable），不可用时退化为 `colorspace` 近似；旋转在此步骤固化。此后的分析和 FFmpeg 兜底渲染都只读代理文件，**不再反复解码 4K HEVC**。
4. **镜头切分**：PySceneDetect `ContentDetector`（自适应阈值）。单镜头长素材也可以使用：编排器会在镜头内部选取任意子区间。
5. **逐帧特征（2 fps）**：运动强度（帧差）、清晰度（拉普拉斯方差）、饱和度、暖色占比（海鲜红/橙）、亮度。归一化后得到**吸睛分**，用于为不同叙事角色（钩子、特写、动作、展示、结尾）选片。
6. **素材近似重复分组**：每个素材生成帧指纹，入库时与已有素材比对；同一段画面的不同编码版本归入同一个 `source_group`。来源层查重以 `source_group` 计算，重新导出一遍素材无法绕过查重。
7. **授权台账**：`license_type`（自拍 / 采购 / 授权 / 未知）。`unknown` 的素材可以剪辑，但**禁止发布**（发布预检会拦截）。

---

## 5. 模块一：创意编排（Planner）

### 5.1 叙事模板（每个模板 = 角色槽位 + 节奏 + 视觉风格 + 文案风格）

| 模板 | 结构 | 适用 |
|---|---|---|
| `hook_reveal` 钩子揭晓 | 悬念钩子 → 细节特写 ×2-3 → 动作 → 全景展示 → 引导 | 全平台 |
| `beat_montage` 快剪卡点 | 6–10 个 0.9–1.6 秒短镜头，运镜和转场踩点 | 抖音、快手 |
| `arrival_story` 到港实拍 | “凌晨刚到港”叙事，环境 → 个体 → 特写 → 门店 | 视频号、抖音 |
| `buying_tips` 挑选攻略 | 问题钩子 → 第 1/2/3 招（编号标签）→ 总结 | 小红书、视频号 |
| `deal_promo` 今日福利 | 价格钩子 → 卖点轰炸 → 价签 → 强 CTA | 快手、抖音 |
| `asmr_immersive` 沉浸式 | 长镜头 + 慢运镜、原声加大、极简文字 | 小红书、抖音 |

模板**轮换**而不是随机：选择该平台近期使用最少的模板，保证结构多样。

### 5.2 选片算法（新鲜度优先）

对每个槽位，在所有镜头上滑动生成候选窗口，打分：

```
score = role_fit(窗口特征, 角色) × quality × freshness × diversity
freshness = exp(-Σ 历史重叠秒数 × 时间衰减(半衰期 14 天) / 窗口时长)
diversity = 本方案中同一 source_group 已占比例越高，惩罚越大
```

按 softmax(score / T) 采样（种子可复现），而不是取最大值，以保证多样性。同一方案内的窗口互不重叠。

### 5.3 视觉变化（真实的创意差异，而非对抗指纹）

- **重新构图**：竖屏素材做 1.15–1.5 倍局部放大，并平移到不同主体（左侧蟹钳或右侧蟹身）；横屏素材在竖屏画布上采用“铺满裁切”或“适配 + 模糊背景”。
- **运镜**：ken_burns / zoom_out_slow / breathe 组合动画，或入场动画（zoom_in、slide、pop）。
- **节奏**：快剪模板 0.9–1.6 秒/镜，沉浸模板 3–5 秒/镜；高光镜头 0.7 倍慢放（素材帧率允许时）。
- **转场**：按模板风格从 49 种剪映式转场中选取（快剪用 zoom/glitch/cam 类，叙事用叠化、模糊类），约 40% 的剪辑点为硬切，避免每个剪辑点都加转场。
- **调色**：整片统一一个滤镜（鲜艳 / 暖阳 / 清新 / 胶片 / 日系），再在小范围内微调饱和度和色温。
- **花字**：钩子标题（大字 + pop 动画，可用〖〗标出一个关键词以强调色显示）、段落字幕（画面细节、步骤编号）、角标（只写该镜头看得见的东西，如“看尖刺”；找不到画面依据就不加角标）、结尾 CTA。
- **文字排版**：按画布宽度和字号估算每行字数，在标点处折行，自动缩小字号直到不超过 2 行（避免 v2 实测中出现的“红帝王 / 蟹”断行）。

### 5.4 文案（Copywriter）

- **模板文案库**（离线可用）：钩子、卖点、步骤、CTA、标题、正文、话题，按模板分类，并带槽位（产品、产地、价格、门店、卖点）。每个平台记录近期用过的钩子，**不重复使用**。
- **视觉场记**（`app/creative/vision.py`）：素材分析完成后，每个镜头抽 3 帧交给视觉模型（`AGENT_VISION_MODEL_*`），只记录看得见的事实（主体、动作、颜色纹理、环境、最抓眼的瞬间、观众可能好奇的问题），并过滤画面证明不了的说法（到港、新鲜、口感、价格）和负面观察。结果按提示词版本缓存在 `material.analysis["vision"]`，生成时缺失会先补齐。
- **LLM 文案**（DeepSeek，`COPYWRITER=llm`）：输入的是**这条视频实际用到的镜头**的画面事实，每条视频随机一个“人设 × 切入角度”。模型给出 3 条钩子、2 条标题候选，卖点字幕和角标必须引用本镜头的事实编号；校验长度、广告法、套话（今日到港、现捞现做、家人们……）、无依据说法和价格，再按与近期文案的字符二元组相似度挑最新颖的一条。不合格先让模型返工一次，仍失败则用画面事实拼出的兜底文案，最后才回退到模板库。
- **平台约束**：小红书标题 ≤ 20 字；抖音 `text` ≤ 1000 字；话题数 ≤ 5。
- **广告法极限词检查**（“最”“第一”“顶级”“国家级”“100%”等）：生成时规避，发布预检时拦截。

### 5.5 生成闭环

```
for attempt in 1..N:
    plan = planner.plan(seed + attempt, 排除上一轮重合最高的窗口)
    L1 = provenance_overlap(plan, 全平台历史作品)
    if L1 合格: break
else: 若最佳方案仍不合格 → 状态 exhausted，提示补拍或扩充素材
render(plan) → cover → L2/L3 指纹查重 → pass / review / reject
```

---

## 6. 模块一：查重引擎（与自身历史作品比对）

### 6.1 三层证据

| 层 | 名称 | 计算 | 不变性 | 说明 |
|---|---|---|---|---|
| L1 | **来源重合度 P** | 新方案各 clip 的 `(source_group, src 区间)` 与历史作品 EDL 求交，按输出时长归一化 | 对镜像/调色/裁剪/变速/转码**完全不变** | 精确，渲染前即可计算；只覆盖本系统生成的作品 |
| L2 | **画面时序覆盖 V** | 每 0.5 秒抽帧，计算**镜像不变 pHash**（灰度 32×32 → 左右对称化 → DCT 取 8×16 偶数列低频 → 中值二值化，共 64 位）；与历史帧汉明距离 ≤ 10 视为匹配；只有处于**时序一致的连续匹配**中（相邻匹配在 0.8–1.25 倍速度范围内单调）的帧才计入覆盖；低信息帧（纯色/黑场）剔除 | 镜像、亮度/色调、轻度裁剪、变速、转码 | 也覆盖外部导入的历史作品 |
| L3 | **音频覆盖 A** | Philips 式音频指纹（5.5 kHz，33 个对数频带能量差 → 32 bit/帧）；以 2 秒为块、根据精确命中投票得到偏移，计算块误码率（BER < 0.35 视为匹配） | 转码、音量 | 同一段 BGM 是常见且合规的用法，因此权重较低 |

### 6.2 判定

```
content = max(P, V)                      # 来源精确证据与感知证据取大
S       = 0.75 × content + 0.25 × A
reject  : content ≥ 0.60 或 S ≥ 0.70
review  : content ≥ 0.35 或 S ≥ 0.45 或 库覆盖率 ≥ 0.85（整体素材新鲜度过低）
pass    : 其余
```

- **两两比对**取最大值，同时计算**库覆盖率**（新视频有多少比例的画面在整个历史库中出现过），用于发现“拼接多个旧作品”的情况。
- **比对范围**：默认覆盖各平台已通过或待发布作品及导入的历史作品，包括同批次的兄弟版本；可通过 `DEDUP_SCOPE=platform` 改为同平台范围。
- **检索**：历史帧哈希常驻内存矩阵（uint64），用 numpy 向量化 XOR + popcount。百万帧量级下一次查询约 1 秒；更大规模时切换到多索引哈希或 faiss 二值索引（已预留接口）。
- **可解释性**：报告列出每个相似历史作品的 P/V/A、重合区间（新视频时间 ↔ 历史视频时间）、对应的素材片段；运营人员可复核并留痕（复核人、时间、意见）。
- **阈值**全部可配置，并应使用真实发布数据回归校准。

### 6.3 后续增强（已预留）

L2 可增加深度拷贝检测特征（SSCD / DINOv2 帧向量 + 余弦相似），更接近平台的鲁棒性；需核对模型权重的商用许可。

---

## 7. 模块二：一键多平台发布

### 7.1 平台能力与适配策略

| 平台 | 方式 | 关键接口（已核对官方文档） |
|---|---|---|
| 抖音 | OpenAPI（需 `video.create.bind` 权限与用户授权；准入受限时降级为人工辅助） | `POST /api/douyin/v1/video/upload_video/?open_id=`（multipart 字段 `video`，>50MB 建议分片，>300MB 必须分片，单片 5–20MB）→ `POST /api/douyin/v1/video/create_video/?open_id=`（JSON：`video_id`、`text`、`cover_tsp`…，返回 `item_id`，创建后进入审核） |
| 快手 | OpenAPI（`user_video_publish`） | `POST /openapi/photo/start_upload` → `{upload_token, endpoint}` → `POST http://{endpoint}/api/upload?upload_token=`（<10MB 直传）或 `/api/upload/fragment?fragment_id=` + `/api/upload/resume` + `/api/upload/complete?fragment_count=` → `POST /openapi/photo/publish`（multipart `cover`、`caption`）→ **异步**，必须回查 `photo_id` |
| 视频号 | 人工辅助（无公开发布 API） | 生成发布包：成片、封面、标题、正文、话题；提供手机端交接页（局域网扫码下载 + 一键复制）；运营确认后回填作品链接 |
| 小红书 | 人工辅助（无发布 API） | 同上；标题 ≤ 20 字，3:4 画幅 |

所有接口地址均可配置，测试环境指向本地模拟平台。

### 7.2 一键发布流程

```
POST /api/publish/one-click {project_id | variant_ids, targets:[{platform, account_id}], schedule?, skip_failed?}
  1. 预检（逐个“成片 × 账号”）：
     成片已通过查重或人工批准 · 文件存在且 SHA-256 一致 · 规格（分辨率/时长/大小/编码）
     · 账号可用、令牌有效（将过期的先刷新）· 素材授权可发布 · 文案长度与广告法用词
     · 账号日配额与最小发布间隔 · 平台熔断状态
  2. 任一阻断项未通过 → 批次 preflight_failed，返回完整报告，不产生任何上传（除非 skip_failed）
  3. 为每个目标创建 Delivery（幂等键 = variant_id + account_id，重复点击不会重复发布）
  4. 入队 publish_step(delivery_id)，按账号节流和定时发布时间设置 run_after
```

### 7.3 Delivery 状态机

```
QUEUED → UPLOADING → UPLOADED → CREATING ─┬─→ SUBMITTED ─(轮询)→ PUBLISHED
                                          │                    └→ PLATFORM_REJECTED
                                          └─(结果不明)→ CREATE_UNKNOWN → 对账 ─┬→ SUBMITTED
                                                                             └→ CREATING（确认未发出才重试）
任意步骤：可重试错误 → 指数退避 + 抖动（429 采用 Retry-After 或更长的基数）→ 超过次数 → FAILED
         鉴权错误 → 刷新令牌 → 仍失败 → NEEDS_AUTH（重新授权后可恢复）
         永久错误（参数/内容违规）→ FAILED（不重试）
人工辅助：QUEUED → AWAITING_OPERATOR → PUBLISHED（运营回填链接）/ CANCELED
```

- **检查点**：上传凭据（upload_token / endpoint / 已上传分片 / video_id）持久化在 Delivery 上。重启后从检查点继续：快手可通过 `resume` 接口续传，抖音分片按序号续传。
- **对账（reconcile）**：创建接口超时或断连时，查询账号近期作品，按标题标记和时间窗匹配，避免重复发布。
- **熔断**：同一平台连续 N 次可重试失败 → 暂停该平台 M 分钟，到期后放行一次试探请求。
- **审计**：每次状态变化都写入 `delivery_events`（原状态 → 新状态、错误分类、平台 logid）。

### 7.4 账号与安全

- OAuth 授权码流程（带 state 防 CSRF）；令牌使用 **Fernet（AES-128-CBC + HMAC）** 加密存储，密钥来自环境变量；提前刷新即将过期的令牌。
- 账号级节流：`min_interval_sec`（默认 600 秒）、`daily_limit`（默认 10）。目的是遵守接口配额和保证内容质量，**不是**模拟真人规避风控。

---

## 8. 数据模型

```
materials ─< shots                         source_group（近似重复分组）
projects ─< variants ─< deliveries ─< delivery_events
               │            └── publish_batches
               ├── fingerprints（variant / history / material 共用；帧哈希、音频指纹以二进制存储）
               └── dedup_reports（P/V/A、Top 匹配、复核记录）
history_items（导入的历史作品）  accounts  oauth_states  bgm_tracks
jobs（持久化队列：queue/kind/payload/status/attempts/run_after/lease_until/dedupe_key）
platform_circuits（熔断状态）
```

关键约束：`materials.sha256` 唯一；`deliveries(variant_id, account_id)` 唯一；`jobs.dedupe_key` 在 queued/running 状态下部分唯一。

---

## 9. 任务队列（替代 Celery）

- 领取：`UPDATE jobs SET status='running', lease_until=?, locked_by=? WHERE id=? AND status='queued'`，乐观并发，SQLite 与 PostgreSQL 通用。
- 心跳：长任务（渲染）每 15 秒续约；回收器把租约过期的任务放回队列，超过最大次数则标记为 dead。
- 去重键：同一 Delivery 同时最多只有一个活跃的 `publish_step`；同一项目同时最多只有一个生成任务。
- 处理函数**幂等**：重复执行同一个 job 不会产生副作用（依据状态机和检查点判断）。

---

## 10. API 概览

| 资源 | 接口 |
|---|---|
| 系统 | `GET /health`、`GET /api/system/info`、`GET /api/jobs` |
| 素材 | `POST /api/materials/upload`、`POST /api/materials/import`（白名单目录）、`GET /api/materials`、`GET/PATCH/DELETE /api/materials/{id}`、`GET /api/materials/{id}/shots`、`POST /api/materials/{id}/reanalyze` |
| BGM | `POST /api/bgm/upload`、`GET /api/bgm` |
| 历史库 | `POST /api/history/upload`、`GET /api/history` |
| 项目 | `POST /api/projects`、`GET /api/projects`、`GET/PATCH /api/projects/{id}`、`POST /api/projects/{id}/generate {platforms, count}` |
| 成片 | `GET /api/variants?project_id=`、`GET /api/variants/{id}`、`PATCH /api/variants/{id}`（文案）、`POST /api/variants/{id}/approve|reject|recheck|reexport` |
| 查重 | `GET /api/dedup/reports?decision=`、`GET /api/dedup/reports/{variant_id}`、`POST /api/dedup/reports/{id}/review` |
| 账号 | `GET/POST /api/accounts`、`PATCH/DELETE /api/accounts/{id}`、`GET /api/accounts/oauth/{platform}/authorize`、`GET /api/accounts/oauth/{platform}/callback` |
| 发布 | `POST /api/publish/preflight`、`POST /api/publish/one-click`、`GET /api/publish/batches[/{id}]`、`GET /api/publish/deliveries`、`POST /api/publish/deliveries/{id}/retry|cancel|confirm` |
| 交接页 | `GET /m/deliveries/{id}?sig=`（手机端：下载成片和封面、复制文案） |

所有 `/api/*` 接口需携带 `X-API-Key`；媒体文件通过有效期为 1 小时的 HMAC 签名 URL 访问。

---

## 11. 部署

| 角色 | 平台 | 命令 |
|---|---|---|
| API | Linux/macOS | `uvicorn app.main:app` |
| 分析 + 发布 worker | Linux/macOS | `python -m app.jobs.worker --queues analyze,dedup,publish` |
| 渲染 worker | **macOS（剪映 me）** / Linux（FFmpeg 兜底） | `python -m app.jobs.worker --queues render` |
| 数据库 | PostgreSQL 15+（单机可用 SQLite WAL） | `DATABASE_URL=postgresql+psycopg://…` |

配置全部通过环境变量（`.env`）提供，见 `backend/.env.example`。

---

## 12. 测试与验收

- **单元测试**：镜像/调色/变速下的指纹不变性、不同内容的区分度、时序覆盖、来源重合、文字折行、队列领取/租约/重试、令牌加密。
- **发布测试**：针对本地**模拟平台服务器**（模拟抖音、快手的接口语义），注入 5xx、超时、429、令牌过期、“创建成功但响应丢失”等故障，验证重试、对账、不重复发布、断点续传。
- **端到端**：使用 examples 中的真实海鲜素材，执行入库 → 多轮生成 → 查重报告 → 一键发布（模拟平台 + 人工通道）。

验收标准：
1. 同一素材的多轮生成中，同平台成片两两之间来源重合度低于阈值，否则系统给出补拍提示；
2. 任一平台令牌失效时，任务进入 `NEEDS_AUTH`，不影响其他平台；
3. 注入故障后，平台侧不出现重复作品；
4. 可以从任一作品反查素材、授权、EDL、查重报告和发布记录。
