"""匹配引擎：硬过滤逐条 / 越界六原因 / 同 seed 同序 / 范围内优先 / 三系数 / auto 端到端。"""
from datetime import timedelta

from app import db
from app.core.clock import clock
from app.models import Commission, Listing, Merchant, Order, User
from app.modules.matching import filters, lottery


def _mk_user(uid=1, allergens=None, credit=100, win=0, wallet=1500, picked=False):
    with db.session_scope() as s:
        u = User(id=uid, nickname=f"u{uid}", home_lat=30.2741, home_lng=120.1551,
                 allergens=allergens or [], wallet_single_limit=wallet,
                 credit_score=credit, win_count_7d=win)
        s.add(u)
        s.commit()
    if picked:
        _mk_order(uid, status="picked_up")
    return uid


def _mk_merchant(mid=1, lat=30.2777, rating=4.6, count=120):
    with db.session_scope() as s:
        m = Merchant(id=mid, name=f"m{mid}", lat=lat, lng=120.1551, rating=rating, rating_count=count)
        s.add(m)
        s.commit()
    return mid


def _mk_commission(uid, cid=1, **kw):
    now = clock.now()
    base = dict(
        user_id=uid, status="active", type="periodic", raw_text="t", rule_card={},
        buy_modes=["blind", "public"], categories=["餐食"], size="不限", merchant_ids=[],
        center_lat=30.2741, center_lng=120.1551, radius_m=1000,
        active_weekdays=list(range(7)), per_order_max=1500,
        valid_from=now - timedelta(days=1), valid_to=now + timedelta(days=7),
    )
    base.update(kw)
    with db.session_scope() as s:
        c = Commission(id=cid, **base)
        s.add(c)
        s.commit()
    return cid


def _mk_listing(mid=1, lid=1, **kw):
    now = clock.now()
    base = dict(
        merchant_id=mid, status="on_sale", mode="public", category="餐食", size="标准",
        items=["便当"], allergens=[], original_price=2000, price=1390,
        qty_total=3, qty_available=3, qty_locked=0,
        pickup_start=now + timedelta(hours=2), pickup_end=now + timedelta(hours=4),
    )
    base.update(kw)
    with db.session_scope() as s:
        l = Listing(id=lid, **base)
        s.add(l)
        s.commit()
    return lid


def _mk_order(uid, status="paid", lid=1, cid=1, mid=1):
    with db.session_scope() as s:
        o = Order(user_id=uid, commission_id=cid, listing_id=lid, merchant_id=mid,
                  amount=1390, status=status, pay_mode="auto")
        s.add(o)
        s.commit()
        return o.id


def _eval(cid=1, lid=1):
    with db.session_scope() as s:
        c = s.get(Commission, cid)
        l = s.get(Listing, lid)
        return filters.evaluate(s, c, l)


def test_hard_filter_mode_category_distance(client):
    _mk_user()
    _mk_merchant()
    _mk_commission(1, buy_modes=["blind"])
    _mk_listing(mode="public")
    assert _eval()[0] is False  # mode 不匹配
    with db.session_scope() as s:
        s.get(Commission, 1).buy_modes = ["blind", "public"]
        s.get(Commission, 1).categories = ["面包烘焙"]
        s.commit()
    assert _eval()[0] is False  # 品类不匹配
    with db.session_scope() as s:
        s.get(Commission, 1).categories = ["餐食"]
        s.commit()
    _mk_merchant(mid=2, lat=30.32)  # ~5km
    _mk_listing(mid=2, lid=2)
    assert _eval(lid=2)[0] is False  # 距离>3000 硬排除


def test_hard_filter_allergen_weekly_occupied(client):
    _mk_user(allergens=["花生"])
    _mk_merchant()
    _mk_commission(1)
    _mk_listing(allergens=["花生"])
    assert _eval()[0] is False  # 明确含过敏原硬排除
    _mk_listing(lid=2, allergens=[])
    with db.session_scope() as s:
        s.get(Commission, 1).weekly_count_max = 1
        s.get(Commission, 1).used_count_week = 1
        s.commit()
    assert _eval(lid=2)[0] is False  # 周次数用完
    with db.session_scope() as s:
        s.get(Commission, 1).used_count_week = 0
        s.commit()
    _mk_order(1, status="paid", lid=2)
    assert _eval(lid=2)[0] is False  # 已占份
    with db.session_scope() as s:
        s.query(Order).filter(Order.listing_id == 2).update({"status": "refunded"})
        s.commit()
    assert _eval(lid=2)[0] is True  # refunded 不占份


def test_overrun_six_reasons(client):
    _mk_user(allergens=["花生"], wallet=1500)
    _mk_merchant(count=5)  # NEW_MERCHANT
    _mk_commission(1, radius_m=500, monthly_amount_max=1000,
                   active_time_start="08:00", active_time_end="10:00")
    # price 1390 > emax? emax=min(1500,1500)=1500 → 不超价；改 price 触发 OVER_PRICE
    _mk_listing(price=1600, original_price=4000, mode="blind", allergens=["未知"])
    hard, in_range, reasons, ctx = _eval()
    assert hard is True and in_range is False
    assert "OVER_PRICE" in reasons       # 1600>1500
    assert "OVER_MONTHLY" in reasons     # 0+1600>1000
    assert "OUT_OF_RADIUS" not in reasons  # dist~400<radius500 → 不触发（另见专项用例）
    assert "NEW_MERCHANT" in reasons     # count5<10
    assert "ALLERGEN_UNKNOWN" in reasons # 未知+花生
    assert "TIME_CONFLICT" in reasons    # 取货窗∩08-10=∅


def test_out_of_radius_overrun(client):
    _mk_user()
    _mk_merchant(lat=30.288)  # ~1550m
    _mk_commission(1, radius_m=1000)
    _mk_listing()
    hard, in_range, reasons, _ = _eval()
    assert hard and not in_range
    assert reasons == ["OUT_OF_RADIUS"]


def test_blind_rating_gate(client):
    _mk_user()
    _mk_merchant(rating=4.2)
    _mk_commission(1)
    _mk_listing(mode="blind", original_price=4000, price=1390)
    assert _eval()[0] is False  # 盲盒评分<4.3 硬排除


def test_lottery_same_seed_same_order_and_factors(client):
    items = [{"commission_id": i, "weight": w} for i, w in enumerate([1.5, 0.5, 1.0, 0.3])]
    a = lottery.lottery(items, "seedabc")
    b = lottery.lottery(items, "seedabc")
    assert [x["commission_id"] for x in a] == [x["commission_id"] for x in b]

    class U:
        credit_score = 100
        win_count_7d = 2
    w_new, f_new = lottery.compute_weight(U(), has_picked_up=False)
    w_old, f_old = lottery.compute_weight(U(), has_picked_up=True)
    assert f_new["new_customer_factor"] == 1.5
    assert f_old["new_customer_factor"] == 1.0
    assert abs(f_new["rotation_factor"] - 1 / 3) < 1e-3
    assert w_new > w_old  # 新客加成


def test_in_range_priority_over_overrun(client):
    _mk_user(uid=1, allergens=[])           # 范围内
    _mk_user(uid=2, allergens=["花生"])      # 越界 ALLERGEN_UNKNOWN
    _mk_merchant()
    _mk_commission(1, cid=1)
    _mk_commission(2, cid=2)
    _mk_listing(mode="blind", original_price=4000, allergens=["未知"])
    from app.modules.matching import engine
    engine.on_published(1)
    with db.session_scope() as s:
        from app.models import MatchCandidate, MatchRound
        r = s.query(MatchRound).filter(MatchRound.listing_id == 1).first()
        cands = s.query(MatchCandidate).filter(MatchCandidate.round_id == r.id).order_by(MatchCandidate.rank).all()
        assert cands[0].in_range is True       # 范围内整体优先
        assert cands[0].user_id == 1
        assert cands[1].in_range is False
        assert "ALLERGEN_UNKNOWN" in cands[1].overrun_reasons


def test_auto_end_to_end(seeded_client):
    from tests.conftest import login
    from app.modules.payment.sim_events import deliver_due

    client = seeded_client
    uh = login(client, "user", "小拾")
    mh = login(client, "merchant", "麦香烘焙")

    r = client.post("/api/agent/parse", json={"text": "每单不超过15元，半径1公里，餐食"}, headers=uh)
    cid = r.json()["commission"]["id"]
    client.post(f"/api/commissions/{cid}/authorize", headers=uh)
    client.post("/api/payment/sim-auth-confirm", json={"commission_id": cid})
    assert client.get(f"/api/commissions/{cid}", headers=uh).json()["status"] == "active"

    now = clock.now()
    draft = {
        "mode": "public", "category": "餐食", "items": ["今日便当"], "allergens": [],
        "original_price": 2000, "price": 1390, "qty_total": 3,
        "pickup_start": (now + timedelta(hours=2)).isoformat(),
        "pickup_end": (now + timedelta(hours=4)).isoformat(),
    }
    r = client.post("/api/merchant/listings", json=draft, headers=mh)
    assert r.status_code == 200, r.text
    lid = r.json()["id"]

    orders = client.get("/api/orders", headers=uh).json()["items"]
    assert len(orders) == 1
    oid = orders[0]["id"]
    assert orders[0]["status"] == "paying"
    assert orders[0]["items"] is None  # 未支付不泄露

    clock.advance(10)
    deliver_due()
    o = client.get(f"/api/orders/{oid}", headers=uh).json()
    assert o["status"] == "paid"
    assert o["pickup_code"] and len(o["pickup_code"]) == 6
    assert o["items"] == ["今日便当"]  # paid 后揭晓
    assert o["match_reason_text"]

    feed = client.get("/api/feed", headers=uh).json()
    assert any(c["type"] == "order_success" for c in feed["cards"])
    c = client.get(f"/api/commissions/{cid}", headers=uh).json()
    assert c["used_count_week"] == 1
    assert c["spent_amount_month"] == 1390

    rounds = client.get(f"/api/listings/{lid}/rounds").json()["rounds"]
    assert rounds and rounds[0]["seed"]
    assert rounds[0]["candidates"][0]["allocated_order_id"] == oid

    # 库存：3 份只分给唯一候选 1 份（占份集限制），其余仍可用
    l = client.get(f"/api/merchant/listings/{lid}", headers=mh).json()
    assert l["qty_available"] == 2
    assert l["qty_locked"] == 1