"""退款：回退恰好一次 floor0 / 终态不复活 / 释放+重匹配 / 重复通知不重复生效。"""
from app import db
from app.core.clock import clock
from app.core.security import sim_sign
from app.models import Commission, FeedCard, Listing, Order
from app.modules.payment import notify
from app.modules.payment.factory import get_adapter
from app.modules.payment.sim_events import deliver_due
from tests.conftest import mk_commission, mk_listing, mk_merchant, mk_order, mk_user


def _refund_once(oid, amount=1390, reason="商家缺货"):
    with db.session_scope() as s:
        get_adapter().refund(s, s.get(Order, oid), amount, reason)
        s.commit()
    clock.advance(5)
    deliver_due()


def test_refund_rollback_exactly_once(client):
    mk_user()
    mk_merchant()
    mk_commission(1, status="completed", used_count_week=1, spent_amount_month=1390)
    mk_listing(qty_total=1, qty_available=0, qty_locked=1, status="sold_out")
    oid = mk_order(status="paid", paid_at=clock.now())
    _refund_once(oid)
    with db.session_scope() as s:
        o = s.get(Order, oid)
        assert o.status == "refunded" and o.refund_amount == 1390
        c = s.get(Commission, 1)
        assert c.used_count_week == 0 and c.spent_amount_month == 0
        assert c.status == "completed"  # 终态不复活
        l = s.get(Listing, 1)
        assert l.qty_locked == 0 and l.qty_available == 1 and l.status == "on_sale"
        assert s.query(FeedCard).filter(FeedCard.type == "refund_notice").count() == 1
    # 重复通知：仅首次生效
    with db.session_scope() as s:
        o = s.get(Order, oid)
        payload = {"order_id": oid, "out_trade_no": o.out_trade_no,
                   "refund_amount": 1390, "reason": "dup", "success": True}
        res = notify.handle(s, "refund", {"payload": payload, "sign": sim_sign(payload)})
        assert res["first_effect"] is False
    with db.session_scope() as s:
        c = s.get(Commission, 1)
        assert c.used_count_week == 0 and c.spent_amount_month == 0
        assert s.query(FeedCard).filter(FeedCard.type == "refund_notice").count() == 1


def test_refund_floor_zero(client):
    mk_user()
    mk_merchant()
    mk_commission(1, status="completed", used_count_week=0, spent_amount_month=500)
    mk_listing(qty_total=1, qty_available=0, qty_locked=1, status="sold_out")
    oid = mk_order(status="paid", paid_at=clock.now())
    _refund_once(oid)
    with db.session_scope() as s:
        c = s.get(Commission, 1)
        assert c.spent_amount_month == 0 and c.used_count_week == 0  # floor 0 不为负


def test_refund_release_and_rematch(client):
    mk_user()
    mk_merchant()
    mk_commission(1)  # active → refunded 不占份可再参与
    mk_listing(qty_total=1, qty_available=0, qty_locked=1, status="sold_out")
    oid = mk_order(status="paid", paid_at=clock.now())
    _refund_once(oid)
    with db.session_scope() as s:
        assert s.get(Order, oid).status == "refunded"
        orders = s.query(Order).filter(Order.listing_id == 1).all()
        assert len(orders) == 2
        new = [o for o in orders if o.id != oid][0]
        assert new.status == "paying"  # 释放后重匹配
        l = s.get(Listing, 1)
        assert l.qty_available == 0 and l.qty_locked == 1