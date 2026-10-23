"""
查重检测服务 - 与历史作品库比对
"""
import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session

from app.models.database import (
    Fingerprint, SimilarityResult, Variant, Project, Delivery, Delivery as Deliv,
)
from app.services.fingerprint import (
    extract_frame_hashes, compute_full_video_hash, hamming_distance,
    hash_similarity, compute_segment_coverage, compute_frame_set_overlap,
    compute_audio_fingerprint, evaluate_similarity, compute_sha256,
)
from app.core.config import SIMILARITY_DECISION_THRESHOLD


class DedupEngine:
    """查重引擎 - 与自身历史作品库做比对"""

    def __init__(self, db: Session):
        self.db = db

    def build_fingerprint(self, video_path: str, variant_id: str = "",
                          segment_list: List[Dict] = None) -> Dict[str, Any]:
        """
        为视频生成完整指纹
        """
        # 全片哈希
        full_hash = compute_full_video_hash(video_path)

        # 帧级哈希
        frame_hashes, frame_times = extract_frame_hashes(video_path)

        # 音频指纹（如有音频轨）
        audio_fp = ""
        try:
            audio_fp = compute_audio_fingerprint(video_path)
        except Exception:
            pass

        fp_data = {
            "variant_id": variant_id,
            "full_hash": full_hash,
            "frame_hashes": frame_hashes,
            "frame_times": frame_times,
            "segment_list": segment_list or [],
            "audio_fingerprint": audio_fp,
        }

        return fp_data

    def save_fingerprint(self, fp_data: Dict[str, Any]) -> Fingerprint:
        """保存指纹到数据库"""
        fp = Fingerprint(
            variant_id=fp_data.get("variant_id", ""),
            full_hash=fp_data.get("full_hash", ""),
            frame_hashes=fp_data.get("frame_hashes", []),
            frame_times=fp_data.get("frame_times", []),
            segment_list=fp_data.get("segment_list", []),
            audio_fingerprint=fp_data.get("audio_fingerprint", ""),
        )
        self.db.add(fp)
        self.db.commit()
        self.db.refresh(fp)
        return fp

    def check_against_history(self, current_fp: Dict[str, Any],
                              exclude_variant_id: str = "") -> Dict[str, Any]:
        """
        与历史指纹库做全量比对
        返回最高相似度结果和决策
        """
        # 获取所有历史指纹
        query = self.db.query(Fingerprint)
        if exclude_variant_id:
            query = query.filter(Fingerprint.variant_id != exclude_variant_id)
        history_fps = query.all()

        if not history_fps:
            return {
                "decision": "pass",
                "decision_score": 0.0,
                "full_hash_distance": 0.0,
                "segment_coverage": 0.0,
                "visual_score": 0.0,
                "audio_score": 0.0,
                "similar_videos": [],
                "details": {"reason": "历史库为空，无重复风险"},
            }

        best_result = None
        similar_videos = []

        for hist_fp in history_fps:
            # 1. 全片哈希汉明距离
            hash_dist = hamming_distance(
                current_fp.get("full_hash", ""),
                hist_fp.full_hash
            )

            # 2. 帧集重叠 (Jaccard)
            current_set = set(current_fp.get("frame_hashes", []))
            hist_set = set(hist_fp.frame_hashes or [])
            visual_score = compute_frame_set_overlap(
                list(current_set), list(hist_set)
            ) if current_set and hist_set else 0.0

            # 3. 时间覆盖度
            seg_coverage = compute_segment_coverage(
                current_fp.get("frame_times", []),
                hist_fp.frame_times or []
            )

            # 4. 音频相似度
            audio_score = 1.0 if (
                current_fp.get("audio_fingerprint") and
                hist_fp.audio_fingerprint and
                current_fp["audio_fingerprint"] == hist_fp.audio_fingerprint
            ) else 0.0

            # 综合评估
            result = evaluate_similarity(hash_dist, seg_coverage, visual_score, audio_score)

            # 记录相似视频
            if result["decision"] in ("review", "reject"):
                similar_videos.append({
                    "variant_id": hist_fp.variant_id,
                    "decision_score": result["decision_score"],
                    "segment_coverage": result["segment_coverage"],
                    "details": result,
                })

            # 取最相似的结果
            if best_result is None or result["decision_score"] > best_result["decision_score"]:
                best_result = {**result, "target_id": hist_fp.variant_id}

        if best_result is None:
            best_result = {
                "decision": "pass",
                "decision_score": 0.0,
                "full_hash_distance": 0.0,
                "segment_coverage": 0.0,
                "visual_score": 0.0,
                "audio_score": 0.0,
            }

        # 保存查重结果
        if best_result.get("target_id"):
            sim_result = SimilarityResult(
                source_id=current_fp.get("variant_id", ""),
                target_id=best_result["target_id"],
                full_hash_distance=best_result["full_hash_distance"],
                segment_coverage=best_result["segment_coverage"],
                visual_score=best_result["visual_score"],
                audio_score=best_result["audio_score"],
                decision_score=best_result["decision_score"],
                decision=best_result["decision"],
                details=best_result,
            )
            self.db.add(sim_result)
            self.db.commit()

        return {
            **best_result,
            "similar_videos": sorted(similar_videos, key=lambda x: x["decision_score"], reverse=True),
            "details": {
                "history_count": len(history_fps),
                "checked_at": str(Path(__file__).name),
            },
        }

    def check_variant(self, variant_id: str) -> Dict[str, Any]:
        """对指定 variant 做查重检测"""
        variant = self.db.query(Variant).filter(Variant.id == variant_id).first()
        if not variant:
            raise ValueError(f"Variant not found: {variant_id}")

        if not variant.file_path or not Path(variant.file_path).exists():
            raise ValueError(f"Video file not found: {variant.file_path}")

        # 生成指纹
        fp_data = self.build_fingerprint(variant.file_path, variant_id)
        self.save_fingerprint(fp_data)

        # 与历史比对
        result = self.check_against_history(fp_data, exclude_variant_id=variant_id)

        # 更新 variant 的查重状态
        if result["decision"] == "reject":
            variant.status = "rejected"
        elif result["decision"] == "review":
            variant.status = "reviewing"

        self.db.commit()

        return result
