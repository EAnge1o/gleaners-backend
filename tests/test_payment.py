"""支付闭环：auto 失败释放续配 / 确认与超时竞态 / 拒绝续配 / 补单关单 / 对账等待通知。"""
from datetime import timedelta

import pytest

from app import db
from app.core.clock import clock
from app.core.errors import Conflict
from app.core.security import sim_sign
from app.models import Listing, Order, SimEvent, User
from app.modules.matching import engine
from app.modules.order import service as order_service
from app.modules.payment import notify
from app.modules.payment.factory import get_adapter
from app.modules.payment.sim_events import deliver_due
from app.modules.scheduler import jobs
from tests.conftest import mk_commission, mk_listing, mk_merchant, mk_order, mk_user


@pytest.fixture(autouse=True)
def _reset_fail_next():
    get_adapter().set_fail_next(0)
    yield
    get_adapter().set_fail_next(0)


def test_auto_fail_next_release_and_continue(client):
    mk_user(uid=1)
    mk_user(uid=2)
    mk_merchant()
    mk_commission(1, cid=1)
    mk_commission(2, cid=2)
    mk_listing(qty_total=2, qty_available=2)
    get_adapter().set_fail_next(1)
    engine.on_published(1)
    with db.session_scope() as s:
        orders = s.query(Order).order_by(Order.id).all()
        ids = [o.id for o in orders]
        # 首份 auto_pay 失败释放 → 续配第二候选；轮耗尽仍有库存 → 新轮重匹配失败者（closed 不占份）
        assert len(orders) == 3
        assert orders[0].status == "closed"
        assert orders[1].status == "paying"
        assert orders[2].status == "paying"
        l = s.get(Listing, 1)
        assert l.qty_available == 0 and l.qty_locked == 2
    clock.advance(10)
    deliver_due()
    with db.session_scope() as s:
        assert s.get(Order, ids[1]).status == "paid"
        assert s.get(Order, ids[2]).status == "paid"


def test_confirm_then_pay_and_replay(client):
    mk_user()
    mk_merchant()
    mk_commission(1)
    now = clock.now()
    mk_listing(qty_total=3, qty_available=2, qty_locked=1)
    oid = mk_order(status="pending_confirm", pay_mode="instant", out_trade_no="OTNC1",
                   confirm_expires_at=now + timedelta(seconds=180))
    with db.session_scope() as s:
        res = order_service.confirm(s, s.get(User, 1), oid)
        assert res["pay_url"] and res["order"]["status"] == "paying"
    with db.session_scope() as s:
        res2 = order_service.confirm(s, s.get(User, 1), oid)
        assert res2["already"] is True and res2["pay_url"]  # 重复确认幂等回放
    jobs.confirm_timeout()  # 已确认单不受超时 job 影响
    with db.session_scope() as s:
        assert s.get(Order, oid).status == "paying"
    with db.session_scope() as s:
        o = s.get(Order, oid)
        payload = {"order_id": oid, "out_trade_no": o.out_trade_no,
                   "alipay_trade_no": "T", "success": True}
        notify.handle(s, "pay", {"payload": payload, "sign": sim_sign(payload)})
    with db.session_scope() as s:
        assert s.get(Order, oid).status == "paid"


def test_confirm_expired_adjudication(client):
    mk_user()
    mk_merchant()
    mk_commission(1, status="paused")  # 隔离重匹配，单测释放语义
    now = clock.now()
    mk_listing(qty_total=3, qty_available=2, qty_locked=1)
    oid = mk_order(status="pending_confirm", pay_mode="instant", out_trade_no="OTNE1",
                   confirm_expires_at=now + timedelta(seconds=180))
    clock.advance(200)  # 已过期，job 未跑
    with db.session_scope() as s:
        with pytest.raises(Conflict):
            order_service.confirm(s, s.get(User, 1), oid)  # confirm 裁决过期 → 关单
    with db.session_scope() as s:
        assert s.get(Order, oid).status == "closed"
        l = s.get(Listing, 1)
        assert l.qty_available == 3 and l.qty_locked == 0
    jobs.confirm_timeout()  # 竞态重复无副作用
    with db.session_scope() as s:
        assert s.get(Order, oid).status == "closed"
        assert s.get(Listing, 1).qty_available == 3


def test_timeout_close_release_rematch(client):
    mk_user()
    mk_merchant()
    mk_commission(1)  # active → closed 不占份可再参与
    now = clock.now()
    mk_listing(qty_total=3, qty_available=2, qty_locked=1)
    oid = mk_order(status="pending_confirm", pay_mode="instant", out_trade_no="OTNT1",
                   confirm_expires_at=now + timedelta(seconds=180))
    clock.advance(200)
    jobs.confirm_timeout()
    with db.session_scope() as s:
        assert s.get(Order, oid).status == "closed"
        orders = s.query(Order).filter(Order.listing_id == 1).all()
        assert len(orders) == 2
        new = [o for o in orders if o.id != oid][0]
        assert new.status == "paying"  # 释放后续配
        l = s.get(Listing, 1)
        assert l.qty_available == 2 and l.qty_locked == 1


def test_reject_release_and_rematch(client):
    mk_user(uid=1)
    mk_user(uid=2)
    mk_merchant()
    mk_commission(1, cid=1, per_order_max=1000)  # user1 OVER_PRICE 越界
    mk_commission(2, cid=2)                       # user2 范围内
    mk_listing(qty_total=2, qty_available=2)
    engine.on_published(1)
    with db.session_scope() as s:
        orders = s.query(Order).all()
        paying = [o for o in orders if o.status == "paying"]
        pending = [o for o in orders if o.status == "pending_confirm"]
        assert len(paying) == 1 and paying[0].user_id == 2  # 范围内优先
        assert len(pending) == 1 and pending[0].user_id == 1
        pid = pending[0].id
    with db.session_scope() as s:
        res = order_service.reject(s, s.get(User, 1), pid)
        assert res["order"]["status"] == "closed"
    with db.session_scope() as s:
        assert s.get(Order, pid).status == "closed"
        orders = s.query(Order).filter(Order.listing_id == 1).all()
        assert len(orders) == 3  # 拒绝释放 → 越界候选再参与
        assert any(o.user_id == 2 and o.status == "paying" for o in orders)
        assert any(o.user_id == 1 and o.status == "pending_confirm" and o.id != pid for o in orders)
    with db.session_scope() as s:
        res2 = order_service.reject(s, s.get(User, 1), pid)
        assert res2["already"] is True  # 重复拒绝幂等


def test_pay_reconcile_close_stale(client):
    mk_user()
    mk_merchant()
    mk_commission(1, status="paused")
    mk_listing(qty_total=1, qty_available=0, qty_locked=1, status="sold_out")
    oid = mk_order(status="paying", out_trade_no="OTNR1",
                   created_at=clock.now() - timedelta(minutes=5))
    jobs.pay_reconcile()
    with db.session_scope() as s:
        assert s.get(Order, oid).status == "closed"  # 补单关单
        l = s.get(Listing, 1)
        assert l.qty_available == 1 and l.qty_locked == 0


def test_pay_reconcile_waits_pending_notify(client):
    mk_user()
    mk_merchant()
    mk_commission(1)
    mk_listing()
    oid = mk_order(status="paying", out_trade_no="OTNW1",
                   created_at=clock.now() - timedelta(minutes=5))
    with db.session_scope() as s:
        s.add(SimEvent(kind="pay_notify",
                       payload={"order_id": oid, "out_trade_no": "OTNW1", "success": True},
                       due_at=clock.now() + timedelta(seconds=2)))
        s.commit()
    jobs.pay_reconcile()
    with db.session_scope() as s:
        assert s.get(Order, oid).status == "paying"  # 通知在队列不关单