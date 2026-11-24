"""余量发布/下架/缺货/需求统计。publish 同事务提交后触发匹配；
off_shelf/out_of_stock 对 paid 逐单退款 + 关未付单释放库存。
"""
from datetime import datetime, timezone

from app.core.clock import clock
from app.core.errors import Forbidden, NotFound, ValidationFailed
from app.core.geo import haversine_m
from app.core.state_machine import conditional_transition, set_status
from app.core.utils import iso
from app.models import Commission, Listing, Merchant, Order
from app.modules.agent.schemas import ListingDraft, validate_or_raise
from app.modules.listing import inventory
from app.modules.payment.factory import get_adapter

BLIND_MIN_RATING = 4.3
PRICE_BANDS = [(0, 1000, "≤10元"), (1000, 1500, "10-15元"), (1500, 2000, "15-20元"), (2000, 10**9, ">20元")]


def _naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def to_dict(db, l: Listing) -> dict:
    m = db.get(Merchant, l.merchant_id)
    return {
        "id": l.id,
        "merchant_id": l.merchant_id,
        "merchant_name": m.name if m else None,
        "status": l.status,
        "mode": l.mode,
        "category": l.category,
        "size": l.size,
        "diet_tags": l.diet_tags,
        "items": l.items,
        "allergens": l.allergens,
        "original_price": l.original_price,
        "price": l.price,
        "qty_total": l.qty_total,
        "qty_available": l.qty_available,
        "qty_locked": l.qty_locked,
        "pickup_start": iso(l.pickup_start),
        "pickup_end": iso(l.pickup_end),
        "current_round_id": l.current_round_id,
        "created_at": iso(l.created_at),
    }


def _owned(db, merchant, lid: int) -> Listing:
    l = db.get(Listing, lid)
    if not l:
        raise NotFound("余量不存在")
    if l.merchant_id != merchant.id:
        raise Forbidden("无权操作他人余量")
    return l


def publish(db, merchant, data: dict) -> dict:
    draft = validate_or_raise(ListingDraft, data)
    if draft.mode == "blind" and merchant.rating < BLIND_MIN_RATING:
        raise ValidationFailed(f"盲盒要求商家评分≥{BLIND_MIN_RATING}，当前 {merchant.rating}")
    l = Listing(
        merchant_id=merchant.id,
        status="on_sale",
        mode=draft.mode,
        category=draft.category,
        size=draft.size,
        diet_tags=draft.diet_tags,
        items=draft.items,
        allergens=draft.allergens,
        original_price=draft.original_price,
        price=draft.price,
        qty_total=draft.qty_total,
        qty_available=draft.qty_total,
        qty_locked=0,
        pickup_start=_naive_utc(draft.pickup_start),
        pickup_end=_naive_utc(draft.pickup_end),
    )
    db.add(l)
    db.flush()
    db.commit()
    from app.modules.matching.engine import on_published

    on_published(l.id)  # 提交后触发匹配
    db.refresh(l)
    return to_dict(db, l)


def list_mine(db, merchant, status: str | None = None) -> list[dict]:
    q = db.query(Listing).filter(Listing.merchant_id == merchant.id)
    if status:
        q = q.filter(Listing.status == status)
    return [to_dict(db, l) for l in q.order_by(Listing.id.desc()).all()]


def _wind_down(db, l: Listing, reason: str):
    """关未付单释放库存 + 对 paid 逐单退款。调用方负责后续状态与 commit。"""
    orders = db.query(Order).filter(Order.listing_id == l.id).all()
    adapter = get_adapter()
    for o in orders:
        if o.status in ("pending_confirm", "paying"):
            if conditional_transition(db, Order, o.id, o.status, "closed"):
                inventory.release_one(db, l.id)
        elif o.status == "paid":
            adapter.refund(db, o, o.amount, reason)


def off_shelf(db, merchant, lid: int) -> dict:
    l = _owned(db, merchant, lid)
    if l.status == "off_shelf":
        return to_dict(db, l)
    _wind_down(db, l, "商家下架")
    l.qty_available = 0
    set_status(db, l, "off_shelf", "listing")
    db.commit()
    db.refresh(l)
    return to_dict(db, l)


def out_of_stock(db, merchant, lid: int) -> dict:
    l = _owned(db, merchant, lid)
    if l.status == "off_shelf":
        return to_dict(db, l)
    _wind_down(db, l, "商家缺货")
    l.qty_available = 0
    set_status(db, l, "off_shelf", "listing")
    db.commit()
    db.refresh(l)
    return to_dict(db, l)


def demand(db, merchant) -> dict:
    """3000m 内 active 委托单按品类×价格档聚合。"""
    rows = db.query(Commission).filter(Commission.status == "active").all()
    by_category: dict[str, int] = {}
    by_band: dict[str, int] = {}
    matrix: dict[tuple, int] = {}
    total = 0
    for c in rows:
        dist = haversine_m(c.center_lat, c.center_lng, merchant.lat, merchant.lng)
        if dist > 3000:
            continue
        total += 1
        band = next(label for lo, hi, label in PRICE_BANDS if lo <= c.per_order_max < hi)
        by_band[band] = by_band.get(band, 0) + 1
        for cat in c.categories or []:
            by_category[cat] = by_category.get(cat, 0) + 1
            matrix[(cat, band)] = matrix.get((cat, band), 0) + 1
    return {
        "merchant_id": merchant.id,
        "radius_m": 3000,
        "total_active": total,
        "by_category": by_category,
        "by_price_band": by_band,
        "matrix": [
            {"category": cat, "price_band": band, "count": n}
            for (cat, band), n in sorted(matrix.items())
        ],
    }