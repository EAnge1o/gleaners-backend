"""幂等：同键同体重放 / 异体 409 / 支付回调重复只生效一次。"""
from app import db
from app.core.security import sim_sign
from app.models import Commission, Order
from app.modules.payment import notify
from tests.conftest import login, mk_commission, mk_listing, mk_merchant, mk_order, mk_user


def test_request_idempotency_replay_and_conflict(seeded_client):
    client = seeded_client
    uh = login(client, "user", "小拾")
    body = {"text": "每单不超过15元，半径1公里，餐食"}
    h = {**uh, "Idempotency-Key": "k1"}
    r1 = client.post("/api/agent/parse", json=body, headers=h)
    assert r1.status_code == 200, r1.text
    r2 = client.post("/api/agent/parse", json=body, headers=h)
    assert r2.status_code == 200 and r2.json() == r1.json()  # 回放
    with db.session_scope() as s:
        assert s.query(Commission).filter(Commission.user_id == 1).count() == 1
    r3 = client.post("/api/agent/parse",
                     json={"text": "每单不超过20元，半径2公里，餐食"}, headers=h)
    assert r3.status_code == 409  # 同键异体


def test_pay_notify_duplicate_once(client):
    mk_user()
    mk_merchant()
    mk_commission(1)
    mk_listing()
    oid = mk_order(status="paying", out_trade_no="OTN1")
    payload = {"order_id": oid, "out_trade_no": "OTN1", "alipay_trade_no": "T1", "success": True}
    raw = {"payload": payload, "sign": sim_sign(payload)}
    with db.session_scope() as s:
        r1 = notify.handle(s, "pay", raw)
    with db.session_scope() as s:
        r2 = notify.handle(s, "pay", raw)
    assert r1["first_effect"] is True and r2["first_effect"] is False
    with db.session_scope() as s:
        c = s.get(Commission, 1)
        assert c.used_count_week == 1 and c.spent_amount_month == 1390  # 只记一次
        o = s.get(Order, oid)
        assert o.status == "paid" and o.pickup_code