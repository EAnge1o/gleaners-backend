from app.models import FeedCard


def push_card(db, type: str, payload: dict, user_id: int | None = None, merchant_id: int | None = None):
    """与业务写操作同事务（调用方负责 commit），保证卡片不丢不重。"""
    card = FeedCard(type=type, payload=payload, user_id=user_id, merchant_id=merchant_id)
    db.add(card)
    db.flush()
    return card