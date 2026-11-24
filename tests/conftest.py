import os

os.environ["ENV"] = "test"
os.environ["SEED_ON_START"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.core.clock import clock  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    db.configure(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    db.Base.metadata.create_all(db.engine)
    clock.reset()
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def login(client, role: str, key: str) -> dict:
    r = client.post("/api/auth/login", json={"role": role, "identity_key": key})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture()
def user_headers(client):
    return login(client, "user", "小拾")


@pytest.fixture()
def merchant_headers(client):
    return login(client, "merchant", "麦香烘焙")


@pytest.fixture()
def seeded_client(client):
    from app.seed import seed

    seed()
    return client


# ---- P3 测试共用造数助手（直接 ORM，绕过 API 便于构造边界状态）----

def mk_user(uid=1, allergens=None, credit=100, win=0, wallet=1500):
    from app.models import User

    with db.session_scope() as s:
        s.add(User(id=uid, nickname=f"u{uid}", home_lat=30.2741, home_lng=120.1551,
                   allergens=allergens or [], wallet_single_limit=wallet,
                   credit_score=credit, win_count_7d=win))
        s.commit()
    return uid


def mk_merchant(mid=1, lat=30.2777, rating=4.6, count=120):
    from app.models import Merchant

    with db.session_scope() as s:
        s.add(Merchant(id=mid, name=f"m{mid}", lat=lat, lng=120.1551,
                       rating=rating, rating_count=count))
        s.commit()
    return mid


def mk_commission(uid=1, cid=1, **kw):
    from datetime import timedelta

    from app.models import Commission

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
        s.add(Commission(id=cid, **base))
        s.commit()
    return cid


def mk_listing(mid=1, lid=1, **kw):
    from datetime import timedelta

    from app.models import Listing

    now = clock.now()
    base = dict(
        merchant_id=mid, status="on_sale", mode="public", category="餐食", size="标准",
        items=["便当"], allergens=[], original_price=2000, price=1390,
        qty_total=3, qty_available=3, qty_locked=0,
        pickup_start=now + timedelta(hours=2), pickup_end=now + timedelta(hours=4),
    )
    base.update(kw)
    with db.session_scope() as s:
        s.add(Listing(id=lid, **base))
        s.commit()
    return lid


def mk_order(uid=1, status="paid", lid=1, cid=1, mid=1, **kw):
    from app.models import Order

    base = dict(user_id=uid, commission_id=cid, listing_id=lid, merchant_id=mid,
                amount=1390, status=status, pay_mode="auto")
    base.update(kw)
    with db.session_scope() as s:
        o = Order(**base)
        s.add(o)
        s.commit()
        return o.id
