"""匹配引擎（正确性核心）。

on_published：发布后开新轮（硬过滤→越界判定→加权抽签→快照）→按份分配。
continue_allocation：释放触发点（确认超时/拒绝、auto_pay 失败、补单关单、下架/缺货退款）
从持久化 phase_cursor 续配，不重抽，逐候选 revalidate。

分配循环（每 listing 锁内，每份一短事务）：范围内建 paying 单+auto_pay（失败释放续配）；
越界建 pending_confirm 锁 3min+推卡，立即继续下一份不阻塞；cursor 到底仍有库存且本轮有
净消耗才开新轮（新 seed），避免空转。范围内候选中途变越界则跳过留新轮。
"""
import logging
from datetime import timedelta

from app import db
from app.core.clock import clock
from app.core.state_machine import conditional_transition
from app.core.utils import fen_to_yuan, gen_out_trade_no, gen_seed, iso
from app.models import Commission, Listing, MatchCandidate, MatchRound, Order
from app.modules.feed.service import push_card
from app.modules.listing import inventory
from app.modules.matching import filters, lottery, snapshot
from app.modules.payment.factory import get_adapter

log = logging.getLogger(__name__)
CONFIRM_LOCK_MINUTES = 3


def _match_reason(c, listing, ctx, cand, seed) -> dict:
    return {
        "commission_id": c.id,
        "category": listing.category,
        "price": listing.price,
        "effective_max": ctx["effective_max"],
        "distance_m": round(ctx["distance_m"]),
        "radius_m": c.radius_m,
        "rating": ctx["merchant"].rating,
        "original_price": listing.original_price,
        "rank": cand.rank,
        "seed": seed,
    }


def humanize_match_reason(mr: dict | None) -> str:
    if not mr:
        return ""
    cmp = "≤" if mr["price"] <= mr["effective_max"] else ">"
    return (
        f"{mr['category']}·{fen_to_yuan(mr['price'])}元{cmp}{fen_to_yuan(mr['effective_max'])}元"
        f"·{mr['distance_m'] / 1000:.1f}公里·评分{mr['rating']}"
    )


def _open_round(listing_id: int) -> int | None:
    with db.session_scope() as s:
        listing = s.get(Listing, listing_id)
        if not listing or listing.status != "on_sale" or listing.qty_available <= 0:
            return None
        commissions = s.query(Commission).filter(Commission.status == "active").all()
        in_range_items, overrun_items = [], []
        for c in commissions:
            hard, in_range, reasons, ctx = filters.evaluate(s, c, listing)
            if not hard:
                continue
            user = ctx["user"]
            has_picked = (
                s.query(Order.id)
                .filter(Order.user_id == user.id, Order.status == "picked_up")
                .first()
                is not None
            )
            weight, factors = lottery.compute_weight(user, has_picked)
            item = {
                "commission_id": c.id,
                "user_id": user.id,
                "in_range": in_range,
                "weight": weight,
                "factors": factors,
                "overrun_reasons": reasons,
            }
            (in_range_items if in_range else overrun_items).append(item)

        seed = gen_seed()
        r = snapshot.create_round(s, listing_id, seed)  # 先写 seed 再抽签
        ranked = lottery.lottery(in_range_items, seed) + lottery.lottery(overrun_items, seed)
        snapshot.write_candidates(s, r.id, ranked)
        listing.current_round_id = r.id
        s.commit()
        return r.id


def _try_allocate_one(listing_id: int, round_id: int, candidate_id: int, in_range_snapshot: bool) -> str:
    with db.session_scope() as s:
        listing = s.get(Listing, listing_id)
        if not listing or listing.status != "on_sale" or listing.qty_available <= 0:
            return "no_stock"
        cand = s.get(MatchCandidate, candidate_id)
        c = s.get(Commission, cand.commission_id)
        hard, in_range, reasons, ctx = filters.evaluate(s, c, listing)
        if not hard:
            return "skip"
        if in_range_snapshot and not in_range:
            return "skip"  # 范围内中途变越界 → 跳过留新轮
        if not inventory.lock_one(s, listing_id):
            return "no_stock"

        rnd = s.get(MatchRound, round_id)
        now = clock.now()
        order = Order(
            user_id=c.user_id,
            commission_id=c.id,
            listing_id=listing_id,
            merchant_id=listing.merchant_id,
            amount=listing.price,
            status="paying" if in_range else "pending_confirm",
            pay_mode="auto" if in_range else "instant",
            overrun_reasons=reasons,
            match_reason=_match_reason(c, listing, ctx, cand, rnd.seed if rnd else None),
        )
        if not in_range:
            order.confirm_expires_at = now + timedelta(minutes=CONFIRM_LOCK_MINUTES)
        s.add(order)
        s.flush()
        order.out_trade_no = gen_out_trade_no(order.id)  # 先写库再调支付
        cand.allocated_order_id = order.id
        if not in_range:
            push_card(s, "overrun_confirm", {
                "order_id": order.id,
                "commission_id": c.id,
                "listing_id": listing_id,
                "merchant_name": ctx["merchant"].name,
                "category": listing.category,
                "price": listing.price,
                "overrun_reasons": reasons,
                "distance_m": round(ctx["distance_m"]),
                "confirm_expires_at": iso(order.confirm_expires_at),
                # 越界卡不泄露盲盒 items
            }, user_id=c.user_id)
        s.commit()
        order_id = order.id

    if in_range:
        with db.session_scope() as s:
            order = s.get(Order, order_id)
            try:
                get_adapter().auto_pay(s, order)
                s.commit()
            except Exception:
                log.exception("auto_pay 失败 order=%s，释放续配", order_id)
                s.rollback()
                inventory.release_one(s, listing_id)
                conditional_transition(s, Order, order_id, "paying", "closed")
                s.commit()
                return "pay_failed"
    return "allocated"


def _allocate(listing_id: int):
    while True:
        with db.session_scope() as s:
            listing = s.get(Listing, listing_id)
            if not listing or listing.status != "on_sale" or listing.qty_available <= 0:
                return
            rid = listing.current_round_id
            rnd = s.get(MatchRound, rid) if rid else None
            if rnd is None or rnd.status == "exhausted":
                need_new = True
                cand_data = []
                cursor = 0
            else:
                need_new = False
                cands = (
                    s.query(MatchCandidate)
                    .filter(MatchCandidate.round_id == rid)
                    .order_by(MatchCandidate.rank)
                    .all()
                )
                cand_data = [(cd.id, cd.rank, cd.in_range) for cd in cands]
                cursor = rnd.phase_cursor

        if need_new:
            if _open_round(listing_id) is None:
                return
            continue

        allocated = 0
        for cid, rank, in_range in cand_data:
            if rank < cursor:
                continue
            res = _try_allocate_one(listing_id, rid, cid, in_range)
            with db.session_scope() as s:
                r = s.get(MatchRound, rid)
                if r:
                    r.phase_cursor = rank + 1
                    s.commit()
            if res == "no_stock":
                return
            if res == "allocated":
                allocated += 1

        with db.session_scope() as s:
            r = s.get(MatchRound, rid)
            if r:
                r.status = "exhausted"
            listing = s.get(Listing, listing_id)
            remaining = listing.qty_available if listing else 0
            on_sale = listing.status == "on_sale" if listing else False
            s.commit()
        # 本轮有净消耗且仍有库存才开新轮，避免候选全跳过时空转
        if remaining > 0 and on_sale and allocated > 0:
            if _open_round(listing_id) is None:
                return
            continue
        return


def on_published(listing_id: int):
    with inventory.listing_lock(listing_id):
        _open_round(listing_id)
        _allocate(listing_id)


def continue_allocation(listing_id: int):
    with inventory.listing_lock(listing_id):
        _allocate(listing_id)