"""
查重检测 API
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.database import SimilarityResult, Fingerprint, Variant
from app.services.dedup import DedupEngine
from app.schemas.models import DedupResponse, SimilarityResultResponse

router = APIRouter(prefix="/api/dedup", tags=["查重检测"])


@router.post("/check/{variant_id}", response_model=DedupResponse)
async def check_dedup(variant_id: str, db: Session = Depends(get_db)):
    """对指定视频版本做查重检测"""
    variant = db.query(Variant).filter(Variant.id == variant_id).first()
    if not variant:
        raise HTTPException(status_code=404, detail="Variant not found")

    dedup = DedupEngine(db)

    try:
        result = dedup.check_variant(variant_id)
        return DedupResponse(
            variant_id=variant_id,
            decision=result["decision"],
            decision_score=result["decision_score"],
            full_hash_distance=result["full_hash_distance"],
            segment_coverage=result["segment_coverage"],
            visual_score=result["visual_score"],
            audio_score=result["audio_score"],
            similar_videos=result.get("similar_videos", []),
            details=result.get("details", {}),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/results", response_model=List[SimilarityResultResponse])
async def list_similarity_results(
    skip: int = 0, limit: int = 100,
    decision: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """相似度检测结果列表"""
    query = db.query(SimilarityResult)
    if decision:
        query = query.filter(SimilarityResult.decision == decision)

    results = query.order_by(SimilarityResult.checked_at.desc()).offset(skip).limit(limit).all()

    return [
        SimilarityResultResponse(
            id=r.id,
            source_id=r.source_id,
            target_id=r.target_id,
            full_hash_distance=r.full_hash_distance,
            segment_coverage=r.segment_coverage,
            visual_score=r.visual_score,
            audio_score=r.audio_score,
            decision_score=r.decision_score,
            decision=r.decision,
            checked_at=r.checked_at,
        )
        for r in results
    ]


@router.get("/results/{result_id}", response_model=SimilarityResultResponse)
async def get_similarity_result(result_id: str, db: Session = Depends(get_db)):
    """获取单次查重结果详情"""
    result = db.query(SimilarityResult).filter(SimilarityResult.id == result_id).first()
    if not result:
        raise HTTPException(status_code=404, detail="Result not found")

    return SimilarityResultResponse(
        id=result.id,
        source_id=result.source_id,
        target_id=result.target_id,
        full_hash_distance=result.full_hash_distance,
        segment_coverage=result.segment_coverage,
        visual_score=result.visual_score,
        audio_score=result.audio_score,
        decision_score=result.decision_score,
        decision=result.decision,
        checked_at=result.checked_at,
    )


@router.post("/build-fingerprint/{variant_id}")
async def build_fingerprint(variant_id: str, db: Session = Depends(get_db)):
    """手动为视频生成指纹"""
    variant = db.query(Variant).filter(Variant.id == variant_id).first()
    if not variant:
        raise HTTPException(status_code=404, detail="Variant not found")

    dedup = DedupEngine(db)
    fp_data = dedup.build_fingerprint(variant.file_path, variant_id)
    fp = dedup.save_fingerprint(fp_data)

    return {
        "fingerprint_id": fp.id,
        "full_hash": fp_data["full_hash"],
        "frame_count": len(fp_data["frame_hashes"]),
        "audio_fingerprint": fp_data.get("audio_fingerprint", ""),
    }


@router.get("/stats")
async def get_dedup_stats(db: Session = Depends(get_db)):
    """查重统计"""
    total_fps = db.query(Fingerprint).count()
    total_results = db.query(SimilarityResult).count()
    pass_count = db.query(SimilarityResult).filter(SimilarityResult.decision == "pass").count()
    review_count = db.query(SimilarityResult).filter(SimilarityResult.decision == "review").count()
    reject_count = db.query(SimilarityResult).filter(SimilarityResult.decision == "reject").count()

    return {
        "total_fingerprints": total_fps,
        "total_checks": total_results,
        "pass_count": pass_count,
        "review_count": review_count,
        "reject_count": reject_count,
        "pass_rate": round(pass_count / total_results * 100, 2) if total_results > 0 else 0,
    }
