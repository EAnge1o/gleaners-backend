"""匹配硬过滤 + 越界判定。

硬过滤（排除，不成为候选）：active 且在有效期/生效星期；mode∈buy_modes；品类规格匹配；
指定商家约束；盲盒双门槛(rating≥4.3 且 original≥price×2.5)；距离>3000；明确含用户过敏原；
周次数已用完；同用户同余量已占份。

越界（保留候选，不自动付，原因全写 overrun_reasons）：OVER_PRICE / OVER_MONTHLY /
OUT_OF_RADIUS(radius<dist≤3000) / NEW_MERCHANT(rating_count<10) /
ALLERGEN_UNKNOWN(用户有过敏原且余量含"未知") / TIME_CONFLICT(取货窗∩生效时段=∅)。

占份集 = {pending_confirm, paying, paid, picked_up, no_show}；closed/refunded 不占份。
"""
from datetime import datetime, time

from app.core.clock import clock
from app.core.geo import haversine_m
from app.models import Commission, Listing, Merchant, Order, User

OCCUPIED_STATUSES = ("pending_confirm", "paying", "paid", "picked_up", "no_show")
MAX_DISTANCE_M = 3000
BLIND_MIN_RATING = 4.3
BLIND_MIN_RATIO = 2.5
NEW_MERCHANT_THRESHOLD = 10


def occupied(db, user_id: int, listing_id: int) -> bool:
    return (
        db.query(Order.id)
        .filter(
            Order.user_id == user_id,
            Order.listing_id == listing_id,
            Order.status.in_(OCCUPIED_STATUSES),
        )
        .first()
        is not None
    )


def _time_conflict(c: Commission, listing: Listing) -> bool:
    """取货窗口与生效时段（按取货当日）无交集 → 冲突。无生效时段则不冲突。"""
    if not c.active_time_start or not c.active_time_end:
        return False
    day = listing.pickup_start.date()
    a_start = datetime.combine(day, time.fromisoformat(c.active_time_start))
    a_end = datetime.combine(day, time.fromisoformat(c.active_time_end))
    p_start, p_end = listing.pickup_start, listing.pickup_end
    return not (p_start < a_end and a_start < p_end)


def evaluate(db, c: Commission, listing: Listing):
    """返回 (hard_pass, in_range, reasons, ctx)。

    ctx 含 distance_m / merchant / user / effective_max，供引擎建单与 match_reason 复用。
    hard_pass=False 时其余字段无意义。
    """
    now = clock.now()
    merchant = db.get(Merchant, listing.merchant_id)
    user = db.get(User, c.user_id)
    dist = haversine_m(c.center_lat, c.center_lng, merchant.lat, merchant.lng)
    effective_max = min(c.per_order_max, user.wallet_single_limit)
    ctx = {
        "distance_m": dist,
        "merchant": merchant,
        "user": user,
        "effective_max": effective_max,
    }

    # ---- 硬过滤 ----
    if c.status != "active":
        return False, False, [], ctx
    if not (c.valid_from <= now <= c.valid_to):
        return False, False, [], ctx
    if now.weekday() not in (c.active_weekdays or []):
        return False, False, [], ctx
    if listing.mode not in (c.buy_modes or []):
        return False, False, [], ctx
    if c.categories and listing.category not in c.categories:
        return False, False, [], ctx
    if c.size and c.size != "不限" and listing.size != c.size:
        return False, False, [], ctx
    if c.merchant_ids and listing.merchant_id not in c.merchant_ids:
        return False, False, [], ctx
    if listing.mode == "blind":
        if merchant.rating < BLIND_MIN_RATING:
            return False, False, [], ctx
        if listing.original_price < listing.price * BLIND_MIN_RATIO:
            return False, False, [], ctx
    if dist > MAX_DISTANCE_M:
        return False, False, [], ctx
    user_allergens = set(user.allergens or [])
    listing_allergens = set(listing.allergens or [])
    # 明确含用户过敏原（排除"未知"占位）→ 硬排除
    if user_allergens and (listing_allergens - {"未知"}) & user_allergens:
        return False, False, [], ctx
    if c.weekly_count_max is not None and c.used_count_week >= c.weekly_count_max:
        return False, False, [], ctx
    if occupied(db, c.user_id, listing.id):
        return False, False, [], ctx

    # ---- 越界判定 ----
    reasons: list[str] = []
    if listing.price > effective_max:
        reasons.append("OVER_PRICE")
    if (
        c.monthly_amount_max is not None
        and c.spent_amount_month + listing.price > c.monthly_amount_max
    ):
        reasons.append("OVER_MONTHLY")
    if dist > c.radius_m:  # dist≤3000 已由硬过滤保证
        reasons.append("OUT_OF_RADIUS")
    if merchant.rating_count < NEW_MERCHANT_THRESHOLD:
        reasons.append("NEW_MERCHANT")
    if user_allergens and "未知" in listing_allergens:
        reasons.append("ALLERGEN_UNKNOWN")
    if _time_conflict(c, listing):
        reasons.append("TIME_CONFLICT")

    return True, not reasons, reasons, ctx