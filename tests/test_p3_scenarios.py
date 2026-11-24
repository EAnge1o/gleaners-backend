"""P3 三场景端到端（API + 虚拟时钟 + jobs）：overrun / refund / no_show。"""
from datetime import timedelta

from app import db
from app.core.clock import clock
from app.models import Commission, User
from app.modules.scheduler.jobs import run_all_once
from tests.conftest import login


def _authorize(client, uh, text="每单不超过15元，半径1公里，餐食"):
    r = client.post("/api/agent/parse", json={"text": text}, headers=uh)
    cid = r.json()["commission"]["id"]
    client.post(f"/api/commissions/{cid}/authorize", headers=uh)
    client.post("/api/payment/sim-auth-confirm", json={"commission_id": cid})
    return cid


def _publish(client, mh, price=1390, mode="public", qty=3, **kw):
    now = clock.now()
    draft = {
        "mode": mode, "category": "餐食", "items": ["今日便当"], "allergens": [],
        "original_price": 4000, "price": price, "qty_total": qty,
        "pickup_start": (now + timedelta(hours=2)).isoformat(),
        "pickup_end": (now + timedelta(hours=4)).isoformat(),
    }
    draft.update(kw)
    r = client.post("/api/merchant/listings", json=draft, headers=mh)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_overrun_scenario(seeded_client):
    client = seeded_client
    uh = login(client, "user", "小拾")
    mh = login(client, "merchant", "麦香烘焙")
    _authorize(client, uh)
    lid = _publish(client, mh, price=1990, mode="blind",
                   original_price=5000, allergens=["未知"])
    orders = client.get("/api/orders", headers=uh).json()["items"]
    assert len(orders) == 1
    o = orders[0]
    assert o["status"] == "pending_confirm"
    assert "OVER_PRICE" in o["overrun_reasons"]      # 1990 > 1500
    assert "ALLERGEN_UNKNOWN" in o["overrun_reasons"]
    assert o["items"] is None                        # 越界不泄露盲盒
    l = client.get(f"/api/merchant/listings/{lid}", headers=mh).json()
    assert l["qty_available"] == 2 and l["qty_locked"] == 1  # 其余份数不阻塞
    feed = client.get("/api/feed", headers=uh).json()
    assert any(c["type"] == "overrun_confirm" for c in feed["cards"])
    # 确认 → 即时付 → paid 揭晓
    r = client.post(f"/api/orders/{o['id']}/confirm", headers=uh)
    assert r.status_code == 200 and r.json()["pay_url"]
    client.post("/api/payment/sim-pay-confirm", json={"order_id": o["id"]})
    paid = client.get(f"/api/orders/{o['id']}", headers=uh).json()
    assert paid["status"] == "paid" and paid["items"] == ["今日便当"]
    assert paid["pickup_code"] and paid["match_reason_text"]
    # 进入取货窗核销
    clock.advance(2 * 3600 + 60)
    r = client.post(f"/api/merchant/orders/{o['id']}/verify", headers=mh)
    assert r.status_code == 200 and r.json()["order"]["status"] == "picked_up"
    with db.session_scope() as s:
        assert s.get(User, 1).credit_score == 100  # cap


def test_refund_scenario(seeded_client):
    client = seeded_client
    uh = login(client, "user", "小拾")
    mh = login(client, "merchant", "麦香烘焙")
    cid = _authorize(client, uh)
    lid = _publish(client, mh, qty=1)
    oid = client.get("/api/orders", headers=uh).json()["items"][0]["id"]
    clock.advance(10)
    run_all_once()
    assert client.get(f"/api/orders/{oid}", headers=uh).json()["status"] == "paid"
    c = client.get(f"/api/commissions/{cid}", headers=uh).json()
    assert c["used_count_week"] == 1 and c["spent_amount_month"] == 1390
    # 商家缺货 → 退款回退
    r = client.post(f"/api/merchant/listings/{lid}/out_of_stock", headers=mh)
    assert r.status_code == 200
    clock.advance(5)
    run_all_once()
    o = client.get(f"/api/orders/{oid}", headers=uh).json()
    assert o["status"] == "refunded" and o["refund_amount"] == 1390
    c = client.get(f"/api/commissions/{cid}", headers=uh).json()
    assert c["used_count_week"] == 0 and c["spent_amount_month"] == 0
    feed = client.get("/api/feed", headers=uh).json()
    assert any(x["type"] == "refund_notice" for x in feed["cards"])
    l = client.get(f"/api/merchant/listings/{lid}", headers=mh).json()
    assert l["status"] == "off_shelf" and l["qty_locked"] == 0


def test_no_show_scenario_three_strikes(seeded_client):
    client = seeded_client
    uh = login(client, "user", "小拾")
    mh = login(client, "merchant", "麦香烘焙")
    cid = _authorize(client, uh)
    for _ in range(3):
        lid = _publish(client, mh, qty=1)
        oid = client.get("/api/orders", headers=uh).json()["items"][0]["id"]
        clock.advance(10)
        run_all_once()
        assert client.get(f"/api/orders/{oid}", headers=uh).json()["status"] == "paid"
        clock.advance(5 * 3600)  # 快进过 pickup_end
        run_all_once()
        o = client.get(f"/api/orders/{oid}", headers=uh).json()
        assert o["status"] == "no_show"
        l = client.get(f"/api/merchant/listings/{lid}", headers=mh).json()
        assert l["status"] == "off_shelf"  # no_show 时 listing 已下架
    with db.session_scope() as s:
        u = s.get(User, 1)
        assert u.credit_score == 40 and u.no_show_streak == 3  # 100−20×3
        assert s.get(Commission, cid).status == "suspended"
    feed = client.get("/api/feed", headers=uh).json()
    assert any(x["type"] == "commission_notice" and x["payload"]["status"] == "suspended"
               for x in feed["cards"])