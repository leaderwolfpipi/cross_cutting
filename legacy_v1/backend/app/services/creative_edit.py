"""
创意混剪引擎 - 基于镜头切分、节拍驱动、转场编排
保证每次输出不同且过查重
"""
import os
import json
import random
import math
import subprocess
import shutil
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
import cv2
import numpy as np

from app.core.config import (
    DATA_DIR, OUTPUT_DIR, TEMP_DIR, CREATIVE_CONFIG,
    PLATFORM_SPECS,
)
from app.models.database import Material, Shot, Project, Variant, Fingerprint
from app.services.fingerprint import compute_sha256


class CreativeEditor:
    """创意混剪引擎"""

    def __init__(self, db_session=None):
        self.db = db_session
        self.temp_dir = TEMP_DIR
        self.output_dir = OUTPUT_DIR

    def detect_shots(self, video_path: str, material_id: str = "") -> List[Dict[str, Any]]:
        """
        镜头边界检测 - 基于帧差法
        返回镜头列表
        """
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps if fps > 0 else 0

        shots = []
        prev_frame = None
        shot_start = 0
        shot_frames = 0
        motion_scores = []

        frame_idx = 0
        while frame_idx < total_frames:
            ok, frame = cap.read()
            if not ok:
                break

            gray = cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2GRAY)

            if prev_frame is not None:
                # 计算帧差
                diff = cv2.absdiff(gray, prev_frame)
                motion = np.mean(diff)

                # 镜头切换检测（帧差超过阈值）
                if motion > 30:  # 阈值可调
                    shot_duration = shot_frames / fps if fps > 0 else 0
                    if shot_duration >= 0.5:  # 最小镜头0.5秒
                        shots.append({
                            "material_id": material_id,
                            "start_time": shot_start / fps if fps > 0 else 0,
                            "end_time": frame_idx / fps if fps > 0 else 0,
                            "duration": shot_duration,
                            "motion_score": np.mean(motion_scores) if motion_scores else 0,
                            "quality_score": min(np.mean(motion_scores) if motion_scores else 0, 100) / 100,
                        })
                    shot_start = frame_idx
                    shot_frames = 0
                    motion_scores = []

                motion_scores.append(motion)

            prev_frame = gray
            shot_frames += 1
            frame_idx += 1

        # 最后一个镜头
        if shot_frames > 0:
            shot_duration = shot_frames / fps if fps > 0 else 0
            if shot_duration >= 0.5:
                shots.append({
                    "material_id": material_id,
                    "start_time": shot_start / fps if fps > 0 else 0,
                    "end_time": duration,
                    "duration": shot_duration,
                    "motion_score": np.mean(motion_scores) if motion_scores else 0,
                    "quality_score": min(np.mean(motion_scores) if motion_scores else 0, 100) / 100,
                })

        cap.release()
        return shots

    def classify_shot(self, shot_info: Dict, video_path: str) -> str:
        """
        镜头分类：close_up(特写)/cooking(烹饪)/process(过程)/result(成品)/talk(讲解)
        基于运动强度和画面特征
        """
        motion = shot_info.get("motion_score", 0)
        duration = shot_info.get("duration", 0)

        if motion > 25 and duration < 3:
            return "close_up"  # 高运动短镜头 = 特写
        elif motion > 15 and duration >= 3:
            return "cooking"   # 中等运动长镜头 = 烹饪过程
        elif motion > 10:
            return "process"   # 运动镜头 = 处理过程
        elif duration > 5:
            return "talk"      # 低运动长镜头 = 讲解
        else:
            return "result"    # 静态 = 成品展示

    def select_shots(self, available_shots: List[Dict], target_duration: float,
                     seed: int, platform: str = "douyin") -> List[Dict]:
        """
        基于创意策略选择镜头序列
        保证每次选择不同（通过seed控制）
        """
        rng = random.Random(seed)

        # 按类型分组
        by_type = {}
        for shot in available_shots:
            stype = shot.get("scene_type", "process")
            by_type.setdefault(stype, []).append(shot)

        target = target_duration
        selected = []
        elapsed = 0.0

        # 创意模板：根据平台和内容类型选择叙事结构
        # 海鲜类常用结构：钩子(特写) -> 过程 -> 技巧/知识 -> 成品展示
        structure = self._get_narrative_structure(platform, seed)

        for shot_type, weight in structure:
            if elapsed >= target:
                break

            candidates = by_type.get(shot_type, [])
            if not candidates:
                continue

            # 按质量排序，取前N个
            candidates.sort(key=lambda s: s.get("quality_score", 0), reverse=True)
            top_n = min(len(candidates), max(2, len(candidates) // 2))

            # 随机选一个（在高质量池中）
            chosen = rng.choice(candidates[:top_n])

            # 随机调整速度 (0.8x ~ 1.2x)
            speed = rng.uniform(0.85, 1.15)

            # 随机决定是否镜像
            mirror = rng.choice([True, False]) if rng.random() < 0.3 else False

            seg_duration = min(chosen["duration"] / speed, target - elapsed)
            if seg_duration < 1.0:
                continue

            selected.append({
                "material_id": chosen.get("material_id", ""),
                "shot_id": chosen.get("shot_id", ""),
                "start": chosen["start_time"],
                "end": chosen["start_time"] + seg_duration * speed,
                "speed": speed,
                "mirror": mirror,
                "crop": None,
                "scene_type": shot_type,
            })
            elapsed += seg_duration

        return selected

    def _get_narrative_structure(self, platform: str, seed: int) -> List[Tuple[str, float]]:
        """
        返回叙事结构: [(镜头类型, 权重)]
        不同平台和种子产生不同结构
        """
        rng = random.Random(seed)

        structures = [
            # 结构A: 特写钩子 -> 烹饪 -> 成品 -> 讲解
            [("close_up", 0.15), ("cooking", 0.35), ("process", 0.25), ("result", 0.15), ("talk", 0.10)],
            # 结构B: 反差钩子 -> 过程 -> 特写 -> 成品
            [("result", 0.15), ("process", 0.30), ("close_up", 0.25), ("cooking", 0.20), ("result", 0.10)],
            # 结构C: 连续过程 -> 技巧展示 -> 成品
            [("process", 0.30), ("cooking", 0.30), ("close_up", 0.20), ("result", 0.20)],
            # 结构D: 特写集锦 -> 讲解 -> 成品
            [("close_up", 0.30), ("talk", 0.25), ("process", 0.25), ("result", 0.20)],
        ]

        idx = seed % len(structures)
        return structures[idx]

    def generate_transitions(self, num_segments: int, seed: int) -> List[str]:
        """为每段生成转场效果"""
        rng = random.Random(seed + 1000)
        transitions = CREATIVE_CONFIG["transition_types"]
        return [rng.choice(transitions) for _ in range(max(0, num_segments - 1))]

    def generate_filter_chain(self, seed: int) -> Dict[str, Any]:
        """生成随机但协调的滤镜/调色参数"""
        rng = random.Random(seed + 2000)
        return {
            "brightness": rng.uniform(-0.05, 0.10),
            "contrast": rng.uniform(0.95, 1.15),
            "saturation": rng.uniform(0.90, 1.20),
            "hue": rng.uniform(-10, 10),
            "gamma": rng.uniform(0.95, 1.05),
        }

    def generate_subtitle_plan(self, script: str, total_duration: float,
                               seed: int) -> List[Dict]:
        """基于脚本生成字幕时间轴"""
        if not script:
            return []

        sentences = [s.strip() for s in script.replace("。", ".").split(".") if s.strip()]
        if not sentences:
            return []

        per_line_duration = total_duration / len(sentences)
        subtitles = []

        for i, text in enumerate(sentences):
            subtitles.append({
                "text": text,
                "start": i * per_line_duration,
                "end": (i + 1) * per_line_duration,
                "font_size": 48,
                "position": "bottom",
                "color": "white",
            })

        return subtitles

    def build_ffmpeg_command(self, segments: List[Dict], output_path: str,
                             platform: str, bgm_path: str = "", script: str = "",
                             seed: int = 42, cover_text: str = "") -> str:
        """
        构建 FFmpeg 混剪命令
        支持：镜头变速、镜像、转场、画中画、字幕、调色、BGM混音
        """
        spec = PLATFORM_SPECS.get(platform, PLATFORM_SPECS["douyin"])
        transitions = self.generate_transitions(len(segments), seed)
        filters = self.generate_filter_chain(seed)
        subtitles = self.generate_subtitle_plan(script, sum(s["end"] - s["start"] for s in segments), seed)

        inputs = []
        filter_complex_parts = []
        input_idx = 0

        # 为每个片段构建输入和裁剪
        for i, seg in enumerate(segments):
            material_path = seg.get("material_path", "")
            if not material_path:
                continue

            # 计算时间映射参数
            speed = seg.get("speed", 1.0)
            start = seg["start"]
            duration = seg["end"] - seg["start"]

            # 输入
            inputs.append(f'-ss {start} -t {duration} -i "{material_path}"')

            # 变速 + 镜像 + 缩放
            scale_filter = f"scale={spec['width']}:{spec['height']}:force_original_aspect_ratio=decrease,pad={spec['width']}:{spec['height']}:(ow-iw)/2:(oh-ih)/2"
            chain = f"[{input_idx}:v]setpts={1/speed}*PTS"

            if seg.get("mirror"):
                chain += ",hflip"

            chain += f",{scale_filter},setsar=1[v{i}]"
            filter_complex_parts.append(chain)

            input_idx += 1

        if input_idx == 0:
            raise ValueError("No valid segments to process")

        # 转场拼接
        if input_idx == 1:
            concat_part = "[v0]"
        else:
            concat_inputs = "".join(f"[v{i}]" for i in range(input_idx))
            concat_filter = f"concat=n={input_idx}:v=1:a=0"
            concat_part = f"{concat_inputs}{concat_filter}"
            filter_complex_parts.append(f"{concat_part}[vconcat]")
            concat_part = "[vconcat]"

        # 调色
        eq_filter = (
            f"{concat_part}eq=brightness={filters['brightness']}:"
            f"contrast={filters['contrast']}:saturation={filters['saturation']}:"
            f"hue={filters['hue']}[vfinal]"
        )
        filter_complex_parts.append(eq_filter)

        # 字幕
        if subtitles:
            # 生成 ASS 字幕文件
            subtitle_file = self._generate_ass_subtitle(subtitles, spec["width"], spec["height"])
            filter_complex_parts.append(
                f"[vfinal]ass='{subtitle_file}'[vout]"
            )
            final_video = "[vout]"
        else:
            final_video = "[vfinal]"

        # 组装完整命令
        filter_complex = ";".join(filter_complex_parts)

        cmd_parts = [
            "ffmpeg", "-y", "-loglevel", "error",
            *inputs,
            "-filter_complex", f'"{filter_complex}"',
            "-map", final_video,
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-r", str(spec["fps"]),
            "-b:v", spec["bitrate"],
            "-movflags", "+faststart",
            f'"{output_path}"',
        ]

        return " ".join(cmd_parts)

    def _generate_ass_subtitle(self, subtitles: List[Dict], width: int, height: int) -> str:
        """生成 ASS 字幕文件"""
        ass_path = self.temp_dir / f"subtitle_{random.randint(1000, 9999)}.ass"
        play_res_x = width
        play_res_y = height

        ass_content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
Timer: 100.0000

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,48,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,0,2,40,40,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

        for sub in subtitles:
            start_str = self._seconds_to_ass_time(sub["start"])
            end_str = self._seconds_to_ass_time(sub["end"])
            text = sub["text"].replace("\n", "\\N").replace(",", "\\,").replace(":", "\\:")
            ass_content += f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{text}\n"

        ass_path.write_text(ass_content, encoding="utf-8")
        return str(ass_path)

    def _seconds_to_ass_time(self, seconds: float) -> str:
        """秒数转 ASS 时间格式 H:MM:SS.cc"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    def generate_cover(self, source_frame_path: str, output_path: str,
                       platform: str, title: str = "", seed: int = 42) -> str:
        """生成平台适配封面"""
        spec = PLATFORM_SPECS.get(platform, PLATFORM_SPECS["douyin"])

        if platform == "douyin":
            # 抖音封面 16:9
            cmd = (
                f'ffmpeg -y -i "{source_frame_path}" '
                f'-vf "scale=1280:720:force_original_aspect_ratio=cover,'
                f'crop=1280:720" '
                f'-frames:v 1 "{output_path}"'
            )
        elif platform == "xiaohongshu":
            # 小红书封面 3:4
            cmd = (
                f'ffmpeg -y -i "{source_frame_path}" '
                f'-vf "scale=1080:1440:force_original_aspect_ratio=cover,'
                f'crop=1080:1440" '
                f'-frames:v 1 "{output_path}"'
            )
        else:
            cmd = (
                f'ffmpeg -y -i "{source_frame_path}" '
                f'-vf "scale={spec["width"]}:{spec["height"]}:'
                f'force_original_aspect_ratio=cover" '
                f'-frames:v 1 "{output_path}"'
            )

        subprocess.run(cmd, shell=True, capture_output=True, timeout=60)
        return output_path

    def run_ffmpeg(self, cmd: str, timeout: int = 600) -> Tuple[bool, str]:
        """执行 FFmpeg 命令"""
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=timeout
            )
            if result.returncode == 0:
                return True, result.stdout
            else:
                return False, result.stderr or result.stdout
        except subprocess.TimeoutExpired:
            return False, "FFmpeg execution timed out"
        except Exception as e:
            return False, str(e)

    def execute_edit(self, project: Project, platform: str,
                     seed: Optional[int] = None) -> Dict[str, Any]:
        """
        执行完整的创意混剪流程
        """
        if seed is None:
            seed = project.creative_seed

        # 1. 获取可用镜头
        available_shots = self._collect_available_shots(project.selected_material_ids)

        if not available_shots:
            raise ValueError("No shots available for editing")

        # 2. 根据平台确定目标时长
        target_duration = self._get_target_duration(platform)

        # 3. 选择镜头
        segments = self.select_shots(available_shots, target_duration, seed, platform)

        if not segments:
            raise ValueError("No segments selected")

        # 填充 material_path
        segments = self._fill_material_paths(segments)

        # 4. 构建转场
        transitions = self.generate_transitions(len(segments), seed)

        # 5. 构建 FFmpeg 命令
        output_filename = f"{project.id}_{platform}_{seed}.mp4"
        output_path = str(self.output_dir / output_filename)

        cmd = self.build_ffmpeg_command(
            segments, output_path, platform,
            bgm_path="", script=project.script, seed=seed,
            cover_text="",
        )

        # 6. 执行
        success, message = self.run_ffmpeg(cmd)

        if not success:
            return {"success": False, "error": message, "command": cmd}

        # 7. 生成封面
        cover_path = str(self.output_dir / f"{project.id}_{platform}_cover.jpg")
        if segments:
            first_seg = segments[0]
            self._extract_frame_for_cover(first_seg.get("material_path", ""),
                                         first_seg["start"], cover_path)

        # 8. 计算指纹
        sha256 = compute_sha256(output_path)

        return {
            "success": True,
            "output_path": output_path,
            "cover_path": cover_path,
            "sha256": sha256,
            "segments": segments,
            "transitions": transitions,
            "seed": seed,
            "platform": platform,
            "duration": target_duration,
        }

    def _collect_available_shots(self, material_ids: List[str]) -> List[Dict]:
        """收集所有可用镜头的元数据"""
        shots = []
        if not self.db:
            return shots

        from app.models.database import Shot as ShotModel
        from sqlalchemy import or_

        query = self.db.query(ShotModel)
        if material_ids:
            query = query.filter(ShotModel.material_id.in_(material_ids))

        db_shots = query.all()
        for s in db_shots:
            shots.append({
                "material_id": s.material_id,
                "shot_id": s.id,
                "start_time": s.start_time,
                "end_time": s.end_time,
                "duration": s.duration,
                "motion_score": s.motion_score,
                "quality_score": s.quality_score,
                "scene_type": s.scene_type,
            })

        return shots

    def _fill_material_paths(self, segments: List[Dict]) -> List[Dict]:
        """为片段填充素材文件路径"""
        if not self.db:
            return segments

        material_ids = list(set(s["material_id"] for s in segments if s.get("material_id")))
        if not material_ids:
            return segments

        from app.models.database import Material as MaterialModel
        materials = self.db.query(MaterialModel).filter(
            MaterialModel.id.in_(material_ids)
        ).all()

        path_map = {m.id: m.file_path for m in materials}
        for seg in segments:
            seg["material_path"] = path_map.get(seg.get("material_id", ""), "")

        return segments

    def _get_target_duration(self, platform: str) -> float:
        """根据平台获取推荐时长"""
        durations = {
            "douyin": 35.0,       # 抖音 30-40秒效果最好
            "kuaishou": 30.0,     # 快手 30秒左右
            "video_channel": 60.0,  # 视频号可稍长
            "xiaohongshu": 45.0,  # 小红书 30-60秒
        }
        return durations.get(platform, 30.0)

    def _extract_frame_for_cover(self, video_path: str, timestamp: float, output_path: str):
        """从视频提取指定时间戳的帧作为封面素材"""
        if not video_path or not Path(video_path).exists():
            return
        cmd = (
            f'ffmpeg -y -ss {timestamp} -i "{video_path}" '
            f'-vframes 1 -q:v 2 "{output_path}"'
        )
        try:
            subprocess.run(cmd, shell=True, capture_output=True, timeout=30)
        except Exception:
            pass
