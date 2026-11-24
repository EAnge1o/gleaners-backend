from fastapi import APIRouter, Depends

from app.db import get_db
from app.modules.matching import snapshot

router = APIRouter(tags=["matching"])


@router.get("/api/listings/{listing_id}/rounds")
def rounds(listing_id: int, db=Depends(get_db)):
    """公平性快照：每轮 seed + 候选顺位/权重/三系数/越界原因/分配结果。"""
    return {"listing_id": listing_id, "rounds": snapshot.round_snapshot(db, listing_id)}