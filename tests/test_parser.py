"""P1 规则链路：兜底解析快照 / 非法追问不入库 / 盲盒价格比 / 授权闭环 / 生命周期。"""
from app import db
from app.models import Commission


def _parse(client, headers, text):
    r = client.post("/api/agent/parse", json={"text": text}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_typical_rule_snapshot(client, user_headers):
    body = _parse(client, user_headers, "帮我蹲附近1公里的面包盲盒，每单不超过15元，每周最多3次，有效期7天")
    assert body["ok"] is True
    c = body["commission"]
    assert c["status"] == "draft"
    assert c["per_order_max"] == 1500
    assert c["radius_m"] == 1000
    assert c["weekly_count_max"] == 3
    assert c["categories"] == ["面包烘焙"]
    assert c["buy_modes"] == ["blind"]
    assert c["rule_card"]["valid_days"] == 7
    feed = client.get("/api/feed", headers=user_headers).json()
    assert any(card["type"] == "rule_card" for card in feed["cards"])


def test_missing_amount_asks_and_not_persisted(client, user_headers):
    body = _parse(client, user_headers, "帮我蹲附近的面包盲盒")
    assert body["ok"] is False
    assert body["questions"]
    with db.session_scope() as s:
        assert s.query(Commission).count() == 0


def test_radius_clamp_and_monthly(client, user_headers):
    body = _parse(client, user_headers, "5公里内，每月不超过100元，每单不超过15元的面包")
    c = body["commission"]
    assert c["radius_m"] == 3000
    assert c["monthly_amount_max"] == 10000
    assert c["per_order_max"] == 1500


def test_weekday_time_window_and_single(client, user_headers):
    body = _parse(client, user_headers, "工作日晚上8点到10点，单次帮我买一份15元以内的便当")
    c = body["commission"]
    assert c["type"] == "single"
    assert c["active_weekdays"] == [0, 1, 2, 3, 4]
    assert c["active_time_start"] == "20:00"
    assert c["active_time_end"] == "22:00"
    assert c["categories"] == ["餐食"]


def test_merchant_blind_draft_ok(client, merchant_headers):
    r = client.post(
        "/api/merchant/listings/parse",
        json={"text": "面包盲盒，售价12.9元，原价35元，共3份，今晚20:00-22:00取货"},
        headers=merchant_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    d = body["draft"]
    assert d["mode"] == "blind"
    assert d["price"] == 1290
    assert d["original_price"] == 3500
    assert d["qty_total"] == 3
    assert d["allergens"] == ["未知"]
    assert d["pickup_start"] and d["pickup_end"]


def test_merchant_blind_ratio_rejected(client, merchant_headers):
    r = client.post(
        "/api/merchant/listings/parse",
        json={"text": "面包盲盒20元，原价30元，3份，20:00-22:00取货"},
        headers=merchant_headers,
    )
    body = r.json()
    assert body["ok"] is False
    assert any("2.5" in q for q in body["questions"])


def test_merchant_missing_fields_asks(client, merchant_headers):
    r = client.post(
        "/api/merchant/listings/parse",
        json={"text": "今天剩了一些面包"},
        headers=merchant_headers,
    )
    body = r.json()
    assert body["ok"] is False
    assert len(body["questions"]) >= 3  # 售价/份数/取货窗


def test_authorize_flow_and_idempotent_notify(client, user_headers):
    cid = _parse(client, user_headers, "每单15元以内的面包盲盒")["commission"]["id"]
    r = client.post(f"/api/commissions/{cid}/authorize", headers=user_headers)
    assert r.status_code == 200, r.text
    assert r.json()["commission"]["status"] == "pending_auth"
    assert r.json()["auth_url"] == f"/auth.html?commission_id={cid}"

    r2 = client.post("/api/payment/sim-auth-confirm", json={"commission_id": cid})
    assert r2.json()["result"] == "success"
    c = client.get(f"/api/commissions/{cid}", headers=user_headers).json()
    assert c["status"] == "active"
    assert c["auth_agreement_no"]

    # 重复确认幂等：状态不变，auth_result 卡只有一张
    r3 = client.post("/api/payment/sim-auth-confirm", json={"commission_id": cid})
    assert r3.status_code == 200
    feed = client.get("/api/feed", headers=user_headers).json()
    assert sum(1 for card in feed["cards"] if card["type"] == "auth_result") == 1


def test_sim_event_auto_delivery_with_virtual_clock(client, user_headers):
    from app.core.clock import clock
    from app.modules.payment.sim_events import deliver_due

    cid = _parse(client, user_headers, "每单15元以内的面包")["commission"]["id"]
    client.post(f"/api/commissions/{cid}/authorize", headers=user_headers)
    deliver_due()  # 未到期不投递
    assert client.get(f"/api/commissions/{cid}", headers=user_headers).json()["status"] == "pending_auth"
    clock.advance(10)
    deliver_due()
    assert client.get(f"/api/commissions/{cid}", headers=user_headers).json()["status"] == "active"


def test_patch_draft_and_lifecycle(client, user_headers):
    cid = _parse(client, user_headers, "每单15元以内的面包盲盒")["commission"]["id"]
    r = client.patch(f"/api/commissions/{cid}", json={"per_order_max": 2000}, headers=user_headers)
    assert r.status_code == 200
    assert r.json()["per_order_max"] == 2000

    client.post(f"/api/commissions/{cid}/authorize", headers=user_headers)
    # 非草稿不可改
    r = client.patch(f"/api/commissions/{cid}", json={"per_order_max": 1000}, headers=user_headers)
    assert r.status_code == 409
    # 非法金额被闸门拦截
    r = client.patch(f"/api/commissions/{cid}", json={"radius_m": 100}, headers=user_headers)
    assert r.status_code == 422

    client.post("/api/payment/sim-auth-confirm", json={"commission_id": cid})
    assert client.post(f"/api/commissions/{cid}/pause", headers=user_headers).json()["status"] == "paused"
    assert client.post(f"/api/commissions/{cid}/resume", headers=user_headers).json()["commission"]["status"] == "active"
    assert client.post(f"/api/commissions/{cid}/cancel", headers=user_headers).json()["status"] == "cancelled"
    # 终态不可逆
    assert client.post(f"/api/commissions/{cid}/pause", headers=user_headers).status_code == 409


def test_quota_endpoint(client, user_headers):
    cid = _parse(client, user_headers, "每单15元以内，每周最多3次，每月不超过100元的面包")["commission"]["id"]
    client.post(f"/api/commissions/{cid}/authorize", headers=user_headers)
    client.post("/api/payment/sim-auth-confirm", json={"commission_id": cid})
    q = client.get("/api/me/quota", headers=user_headers).json()
    item = next(i for i in q["items"] if i["commission_id"] == cid)
    assert item["weekly"] == {"used": 0, "max": 3}
    assert item["monthly"] == {"used": 0, "max": 10000}
