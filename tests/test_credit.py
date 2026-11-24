"""信用：+1 cap100 / −20 floor0 / streak3→suspended / 重授权清零 / 核销幂等与守卫。"""
from datetime import timedelta

import pytest

from app import db
from app.core.clock import clock
from app.core.errors import Conflict, Forbidden, NotFound
from app.models import Commission, CreditLog, FeedCard, Listing, Merchant, User
from app.modules.pickup import credit, service as pickup_service
from tests.conftest import mk_commission, mk_listing, mk_merchant, mk_order, mk_user


def test_picked_up_cap_and_streak_reset(client):
    mk_user(credit=99)
    mk_merchant()
    mk_commission(1)
    mk_listing()
    oid = mk_order(status="paid", paid_at=clock.now())
    with db.session_scope() as s:
        u = s.get(User, 1)
        u.no_show_streak = 2
        credit.on_picked_up(s, u, oid)
        s.commit()
    with db.session_scope() as s:
        u = s.get(User, 1)
        assert u.credit_score == 100 and u.no_show_streak == 0 and u.win_count_7d == 1
        credit.on_picked_up(s, u, oid)  # cap 100
        s.commit()
    with db.session_scope() as s:
        assert s.get(User, 1).credit_score == 100
        logs = s.query(CreditLog).filter(CreditLog.user_id == 1).order_by(CreditLog.id).all()
        assert [l.delta for l in logs] == [1, 0]  # 记实际生效增量


def test_no_show_floor_and_suspend(client):
    mk_user(credit=10)
    mk_merchant()
    mk_commission(1, cid=1)                      # active
    mk_commission(1, cid=2, status="paused")
    mk_commission(1, cid=3, status="completed")  # 终态不受影响
    mk_listing()
    with db.session_scope() as s:
        credit.on_no_show(s, s.get(User, 1), 101)
        s.commit()
    with db.session_scope() as s:
        u = s.get(User, 1)
        assert u.credit_score == 0 and u.no_show_streak == 1  # floor 0
        assert s.get(Commission, 1).status == "active"
        credit.on_no_show(s, u, 102)
        credit.on_no_show(s, u, 103)  # streak 3 → suspended
        s.commit()
    with db.session_scope() as s:
        assert s.get(User, 1).no_show_streak == 3
        assert s.get(Commission, 1).status == "suspended"
        assert s.get(Commission, 2).status == "suspended"
        assert s.get(Commission, 3).status == "completed"
        cards = s.query(FeedCard).filter(FeedCard.type == "commission_notice").all()
        assert len(cards) == 2
        assert s.query(CreditLog).filter(CreditLog.reason == "no_show").count() == 3


def test_reauth_clears_streak(seeded_client):
    from tests.conftest import login

    client = seeded_client
    uh = login(client, "user", "小拾")
    r = client.post("/api/agent/parse", json={"text": "每单不超过15元，半径1公里，餐食"}, headers=uh)
    cid = r.json()["commission"]["id"]
    with db.session_scope() as s:
        s.get(User, 1).no_show_streak = 3
        s.get(Commission, cid).status = "suspended"
        s.commit()
    client.post(f"/api/commissions/{cid}/authorize", headers=uh)
    client.post("/api/payment/sim-auth-confirm", json={"commission_id": cid})
    with db.session_scope() as s:
        assert s.get(Commission, cid).status == "active"
        assert s.get(User, 1).no_show_streak == 0


def test_verify_idempotent_and_consume(client):
    mk_user(credit=99)
    mk_merchant()
    mk_commission(1)
    now = clock.now()
    mk_listing(pickup_start=now - timedelta(hours=1), pickup_end=now + timedelta(hours=1),
               qty_total=1, qty_available=0, qty_locked=1, status="sold_out")
    oid = mk_order(status="paid", paid_at=now, pickup_code="123456")
    with db.session_scope() as s:
        r1 = pickup_service.verify(s, s.get(Merchant, 1), code="123456")
        assert r1["already"] is False and r1["order"]["status"] == "picked_up"
    with db.session_scope() as s:
        r2 = pickup_service.verify(s, s.get(Merchant, 1), order_id=oid)
        assert r2["already"] is True and r2["order"]["status"] == "picked_up"
    with db.session_scope() as s:
        u = s.get(User, 1)
        assert u.credit_score == 100 and u.win_count_7d == 1
        assert s.query(CreditLog).filter(CreditLog.user_id == 1).count() == 1
        l = s.get(Listing, 1)
        assert l.qty_locked == 0 and l.qty_available == 0  # 份额消耗不回补


def test_verify_guards(client):
    mk_user()
    mk_merchant()
    mk_merchant(mid=2, lat=30.278)
    mk_commission(1)
    now = clock.now()
    mk_listing(pickup_start=now + timedelta(hours=2), pickup_end=now + timedelta(hours=4))
    oid = mk_order(status="paid", paid_at=now, pickup_code="654321")
    with db.session_scope() as s:
        with pytest.raises(Forbidden):
            pickup_service.verify(s, s.get(Merchant, 2), order_id=oid)  # 非归属商家
    with db.session_scope() as s:
        with pytest.raises(Conflict):
            pickup_service.verify(s, s.get(Merchant, 1), order_id=oid)  # 不在时间窗
    with db.session_scope() as s:
        with pytest.raises(NotFound):
            pickup_service.verify(s, s.get(Merchant, 1), code="000000")  # 无效码