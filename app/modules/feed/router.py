from fastapi import APIRouter, Depends

from app.core.clock import clock
from app.core.utils import iso
from app.db import get_db
from app.models import FeedCard
from app.modules.identity.deps import get_current_user

router = APIRouter(tags=["feed"])


@router.get("/api/feed")
def get_feed(since: int = 0, limit: int = 50, user=Depends(get_current_user), db=Depends(get_db)):
    rows = (
        db.query(FeedCard)
        .filter(FeedCard.id > since, FeedCard.user_id == user.id)
        .order_by(FeedCard.id)
        .limit(limit)
        .all()
    )
    cards = [
        {"id": c.id, "type": c.type, "payload": c.payload, "created_at": iso(c.created_at)}
        for c in rows
    ]
    return {"server_now": iso(clock.now()), "cards": cards}