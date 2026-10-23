"""
视频指纹生成与相似度计算
"""
import hashlib
import numpy as np
from pathlib import Path
import cv2
from PIL import Image
import imagehash
from typing import List, Tuple, Dict, Any, Optional

from app.core.config import (
    FINGERPRINT_FRAME_STEP, FINGERPRINT_HASH_SIZE,
    SIMILARITY_FULL_HASH_THRESHOLD, SIMILARITY_SEGMENT_COVERAGE_THRESHOLD,
    SIMILARITY_DECISION_THRESHOLD,
)


def compute_sha256(file_path: str) -> str:
    """计算文件 SHA-256"""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_frame_hashes(video_path: str, step_sec: float = FINGERPRINT_FRAME_STEP,
                         hash_size: int = FINGERPRINT_HASH_SIZE) -> Tuple[List[str], List[float]]:
    """
    从视频中按固定间隔抽取帧并计算感知哈希
    返回: (frame_hashes, frame_times)
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps if fps > 0 else 0

    step = max(int(fps * step_sec), 1)
    frame_hashes = []
    frame_times = []

    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step == 0:
            # 转灰度 -> 缩小 -> pHash
            gray = cv2.cvtColor(cv2.resize(frame, (128, 128)), cv2.COLOR_BGR2GRAY)
            pil_img = Image.fromarray(gray)
            phash = str(imagehash.phash(pil_img, hash_size=hash_size))
            frame_hashes.append(phash)
            frame_times.append(idx / fps)
        idx += 1

    cap.release()
    return frame_hashes, frame_times


def extract_segment_fingerprints(video_path: str, segments: List[Dict]) -> List[Dict]:
    """
    对指定片段区间提取指纹
    segments: [{"material_id": str, "shot_id": str, "start": float, "end": float,
                "speed": float, "mirror": bool, "crop": dict}]
    """
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    seg_fps = []

    for seg in segments:
        start_frame = int(seg["start"] * fps)
        end_frame = int(seg["end"] * fps)
        seg_hashes = []

        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        for f in range(start_frame, min(end_frame, start_frame + int(fps * 5))):
            ok, frame = cap.read()
            if not ok:
                break
            # 应用镜像
            if seg.get("mirror"):
                frame = cv2.flip(frame, 1)
            # 应用裁剪
            if seg.get("crop"):
                h, w = frame.shape[:2]
                x1 = int(seg["crop"].get("x", 0) * w)
                y1 = int(seg["crop"].get("y", 0) * h)
                x2 = int(seg["crop"].get("x2", 1) * w)
                y2 = int(seg["crop"].get("y2", 1) * h)
                frame = frame[y1:y2, x1:x2]

            gray = cv2.cvtColor(cv2.resize(frame, (64, 64)), cv2.COLOR_BGR2GRAY)
            phash = str(imagehash.phash(Image.fromarray(gray), hash_size=8))
            seg_hashes.append(phash)

        seg_fps.append({
            "material_id": seg.get("material_id", ""),
            "shot_id": seg.get("shot_id", ""),
            "start": seg["start"],
            "end": seg["end"],
            "speed": seg.get("speed", 1.0),
            "mirror": seg.get("mirror", False),
            "hashes": seg_hashes,
        })

    cap.release()
    return seg_fps


def compute_full_video_hash(video_path: str) -> str:
    """计算全片感知哈希"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps if fps > 0 else 0

    # 采样关键帧（最多30帧）
    sample_count = min(30, total_frames)
    if sample_count <= 0:
        return ""

    step = max(total_frames // sample_count, 1)
    sampled_hashes = []

    for i in range(0, total_frames, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, frame = cap.read()
        if not ok:
            continue
        gray = cv2.cvtColor(cv2.resize(frame, (64, 64)), cv2.COLOR_BGR2GRAY)
        h = imagehash.phash(Image.fromarray(gray), hash_size=8)
        sampled_hashes.append(str(h))

    cap.release()

    # 将所有帧哈希拼接后做二次哈希
    combined = "".join(sampled_hashes)
    return hashlib.md5(combined.encode()).hexdigest()


def hamming_distance(hash1: str, hash2: str) -> int:
    """计算两个十六进制哈希的汉明距离"""
    if not hash1 or not hash2:
        return 999
    try:
        return bin(int(hash1, 16) ^ int(hash2, 16)).count("1")
    except ValueError:
        return 999


def hash_similarity(hash1: str, hash2: str, hash_size: int = 8) -> float:
    """两个哈希的相似度 (0~1, 1=完全相同)"""
    max_dist = hash_size * hash_size
    dist = hamming_distance(hash1, hash2)
    return max(0, 1 - dist / max_dist)


def compute_segment_coverage(times_a: List[float], times_b: List[float],
                             max_gap: float = 1.5) -> float:
    """
    计算两段视频帧序列的时间覆盖度
    基于匹配帧的时间连续性
    """
    if not times_a or not times_b:
        return 0.0

    # 找匹配对
    pairs = []
    for ta in times_a:
        for tb in times_b:
            if abs(ta - tb) <= max_gap:
                pairs.append((ta, tb))

    if not pairs:
        return 0.0

    # 按时间排序
    pairs.sort(key=lambda p: (p[0], p[1]))

    # 找最长连续匹配区间
    groups = []
    current_group = [pairs[0]]

    for i in range(1, len(pairs)):
        prev, cur = pairs[i - 1], pairs[i]
        if (abs(cur[0] - prev[0]) <= max_gap and
                abs(cur[1] - prev[1]) <= max_gap):
            current_group.append(cur)
        else:
            groups.append(current_group)
            current_group = [cur]

    groups.append(current_group)

    # 计算最长匹配区间的覆盖率
    max_coverage = 0.0
    for g in groups:
        time_span_a = max(p[0] for p in g) - min(p[0] for p in g)
        time_span_b = max(p[1] for p in g) - min(p[1] for p in g)
        max_coverage = max(max_coverage, time_span_a, time_span_b)

    duration_a = max(times_a) - min(times_a) if len(times_a) > 1 else 1
    duration_b = max(times_b) - min(times_b) if len(times_b) > 1 else 1

    return min(max_coverage / max(duration_a, duration_b), 1.0)


def compute_frame_set_overlap(hashes_a: List[str], hashes_b: List[str]) -> float:
    """计算两组帧哈希的Jaccard相似度"""
    if not hashes_a or not hashes_b:
        return 0.0
    set_a = set(hashes_a)
    set_b = set(hashes_b)
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def compute_audio_fingerprint(audio_path: str) -> str:
    """计算音频指纹（简化版 - 基于频谱特征）"""
    try:
        import librosa
        y, sr = librosa.load(audio_path, sr=22050, duration=60)
        # 使用MFCC作为音频特征
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
        # 量化为指纹
        quantized = (mfcc > 0).astype(int)
        fingerprint = "".join(str(b) for row in quantized for b in row)
        return hashlib.md5(fingerprint.encode()).hexdigest()
    except Exception:
        return ""


def compute_decision_score(segment_coverage: float, visual_score: float,
                           audio_score: float) -> float:
    """
    综合决策分: S = 0.50*F + 0.25*V + 0.25*A
    """
    return (0.50 * segment_coverage +
            0.25 * visual_score +
            0.25 * audio_score)


def evaluate_similarity(full_hash_dist: float, segment_coverage: float,
                        visual_score: float, audio_score: float) -> Dict[str, Any]:
    """
    综合评估相似度，返回决策结果
    """
    decision_score = compute_decision_score(segment_coverage, visual_score, audio_score)

    if segment_coverage >= 0.70 and decision_score >= 0.75:
        decision = "reject"  # 极可能重复
    elif (segment_coverage >= 0.40 and segment_coverage < 0.70) or \
         (decision_score >= 0.55 and decision_score < 0.75):
        decision = "review"  # 需要人工复核
    else:
        decision = "pass"  # 放行

    return {
        "decision": decision,
        "decision_score": round(decision_score, 4),
        "full_hash_distance": round(full_hash_dist, 4),
        "segment_coverage": round(segment_coverage, 4),
        "visual_score": round(visual_score, 4),
        "audio_score": round(audio_score, 4),
    }
