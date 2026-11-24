import uuid
from datetime import timedelta

from app import db
from app.core.clock import clock
from app.models import (
    AppMeta,
    Commission,
    CreditLog,
    FeedCard,
    IdempotencyRecord,
    Listing,
    MatchCandidate,
    MatchRound,
    Merchant,
    Order,
    PayLog,
    SimEvent,
    User,
)

CENTER = (30.2741, 120.1551)

_CLEAR_ORDER = [
    Order, CreditLog, PayLog, MatchCandidate, MatchRound, FeedCard,
    SimEvent, IdempotencyRecord, Listing, Commission, Merchant, User, AppMeta,
]


def seed():
    """幂等：先清表再装载固定种子身份。"""
    with db.session_scope() as s:
        for model in _CLEAR_ORDER:
            s.query(model).delete()
        s.commit()
    clock.reset()

    now = clock.now()
    with db.session_scope() as s:
        u1 = User(id=1, nickname="小拾", home_lat=CENTER[0], home_lng=CENTER[1],
                  allergens=["花生"], diet_tags=[], wallet_single_limit=1500, credit_score=100)
        u2 = User(id=2, nickname="阿穗", home_lat=CENTER[0], home_lng=CENTER[1],
                  allergens=[], diet_tags=[], wallet_single_limit=3000, credit_score=100,
                  win_count_7d=2)
        m1 = Merchant(id=1, name="麦香烘焙", address="文一西路 100 号", lat=30.2777, lng=120.1551,
                      rating=4.6, rating_count=120, pay_channel="sandbox")
        m2 = Merchant(id=2, name="绿源生鲜", address="文一西路 2600 号", lat=30.2975, lng=120.1551,
                      rating=4.5, rating_count=5, pay_channel="sandbox")
        m3 = Merchant(id=3, name="鲜味便当", address="文一西路 900 号", lat=30.2822, lng=120.1551,
                      rating=4.2, rating_count=80, pay_channel="sandbox")
        s.add_all([u1, u2, m1, m2, m3])
        s.commit()

        # 阿穗历史成交：演示公平降权（win_count_7d=2）vs 小拾新客加成（从未成交）
        hist_c = Commission(
            user_id=2, status="completed", type="single", raw_text="历史便当",
            rule_card={}, buy_modes=["public"], categories=["餐食"], size="标准",
            merchant_ids=[], center_lat=CENTER[0], center_lng=CENTER[1], radius_m=1000,
            active_weekdays=list(range(7)), per_order_max=2000,
            valid_from=now - timedelta(days=3), valid_to=now - timedelta(days=1),
            used_count_week=0, spent_amount_month=0,
        )
        s.add(hist_c)
        s.commit()
        hist_l = Listing(
            merchant_id=1, status="off_shelf", mode="public", category="餐食", size="标准",
            items=["历史便当"], allergens=[], original_price=2000, price=1000,
            qty_total=2, qty_available=0, qty_locked=0,
            pickup_start=now - timedelta(days=2), pickup_end=now - timedelta(days=1),
        )
        s.add(hist_l)
        s.commit()
        for i in range(2):
            s.add(Order(
                user_id=2, commission_id=hist_c.id, listing_id=hist_l.id, merchant_id=1,
                amount=1000, status="picked_up", pay_mode="auto",
                out_trade_no=f"HIST{uuid.uuid4().hex[:10]}",
                paid_at=now - timedelta(days=1), picked_up_at=now - timedelta(days=1),
            ))
        s.commit()


def seed_if_empty():
    with db.session_scope() as s:
        if s.query(User).count() == 0:
            need = True
        else:
            need = False
    if need:
        seed()