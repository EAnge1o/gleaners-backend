"""原子防超卖：20 线程并发 lock_one 恰好 qty 成功；释放回补 sold_out→on_sale。"""
import threading

from app import db
from app.models import Listing, Merchant
from app.modules.listing import inventory


def test_concurrent_lock_exactly_qty(client):
    from app.core.clock import clock

    qty = 5
    with db.session_scope() as s:
        m = Merchant(name="并发商家", lat=30.2741, lng=120.1551, rating=4.6, rating_count=100)
        s.add(m)
        s.flush()
        now = clock.now()
        l = Listing(
            merchant_id=m.id, status="on_sale", mode="public", category="餐食",
            items=["x"], allergens=[], original_price=2000, price=1000,
            qty_total=qty, qty_available=qty, qty_locked=0,
            pickup_start=now, pickup_end=now,
        )
        s.add(l)
        s.commit()
        lid = l.id

    results = []
    lock = threading.Lock()

    def worker():
        with db.session_scope() as s:
            ok = inventory.lock_one(s, lid)
            s.commit()
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(results) == qty
    with db.session_scope() as s:
        l = s.get(Listing, lid)
        assert l.qty_available == 0
        assert l.qty_locked == qty
        assert l.status == "sold_out"


def test_release_restores_on_sale(client):
    from app.core.clock import clock

    with db.session_scope() as s:
        m = Merchant(name="释放商家", lat=30.2741, lng=120.1551, rating=4.6, rating_count=100)
        s.add(m)
        s.flush()
        now = clock.now()
        l = Listing(
            merchant_id=m.id, status="on_sale", mode="public", category="餐食",
            items=["x"], allergens=[], original_price=2000, price=1000,
            qty_total=1, qty_available=1, qty_locked=0,
            pickup_start=now, pickup_end=now,
        )
        s.add(l)
        s.commit()
        lid = l.id

    with db.session_scope() as s:
        assert inventory.lock_one(s, lid) is True
        s.commit()
    with db.session_scope() as s:
        assert s.get(Listing, lid).status == "sold_out"
        inventory.release_one(s, lid)
        s.commit()
    with db.session_scope() as s:
        l = s.get(Listing, lid)
        assert l.status == "on_sale"
        assert l.qty_available == 1
        assert l.qty_locked == 0