"""回调统一入口：验签 → 落 pay_log 原文审计 → 条件更新幂等 → 仅首次执行副作用。
handle 自行 commit（调用方提供会话）。重复通知返回 success 但不重复生效。
"""
import json

from app.core.clock import clock
from app.core.errors import Forbidden, NotFound
from app.core.security import sim_verify
from app.core.state_machine import conditional_transition
from app.core.utils import iso
from app.models import Commission, Listing, Merchant, Order, PayLog
from app.modules.feed.service import push_card


def handle(db, kind: str, raw: dict) -> dict:
    payload = raw.get("payload") or {}
    sign = raw.get("sign", "")
    if not sim_verify(payload, sign):
        raise Forbidden("回调验签失败")

    db.add(PayLog(
        type="notify",
        commission_id=payload.get("commission_id"),
        order_id=payload.get("order_id"),
        request_body=json.dumps(raw, ensure_ascii=False),
        simulated=True,
    ))

    if kind == "auth":
        from app.modules.commission import service as commission_service

        first = commission_service.on_auth_notify(db, payload)
        db.commit()
        return {"result": "success", "first_effect": first}

    if kind == "pay":
        return _handle_pay(db, payload)

    if kind == "refund":
        return _handle_refund(db, payload)

    raise NotFound(f"未知回调类型 {kind}")


def _find_order(db, payload: dict) -> Order | None:
    otn = payload.get("out_trade_no")
    if otn:
        o = db.query(Order).filter(Order.out_trade_no == otn).first()
        if o:
            return o
    oid = payload.get("order_id")
    return db.get(Order, int(oid)) if oid else None


def _handle_pay(db, payload: dict) -> dict:
    from app.modules.commission import service as commission_service
    from app.modules.matching.engine import humanize_match_reason
    from app.modules.pickup.service import generate_pickup_code, qr_content

    order = _find_order(db, payload)
    if not order:
        db.commit()
        return {"result": "success", "first_effect": False}

    if not bool(payload.get("success", True)):
        ok = conditional_transition(db, Order, order.id, "paying", "closed")
        if ok:
            from app.modules.listing import inventory

            inventory.release_one(db, order.listing_id)
        db.commit()
        if ok:
            from app.modules.matching.engine import continue_allocation

            continue_allocation(order.listing_id)
        return {"result": "success", "first_effect": ok}

    ok = conditional_transition(
        db, Order, order.id, "paying", "paid",
        paid_at=clock.now(), alipay_trade_no=payload.get("alipay_trade_no"),
    )
    if ok:
        db.refresh(order)
        c = db.get(Commission, order.commission_id)
        c.used_count_week += 1
        c.spent_amount_month += order.amount
        order.pickup_code = generate_pickup_code(db)
        order.qr_content = qr_content(order.id, order.pickup_code)
        commission_service.check_completion(db, c)
        listing = db.get(Listing, order.listing_id)
        merchant = db.get(Merchant, order.merchant_id)
        push_card(db, "order_success", {
            "order_id": order.id,
            "commission_id": c.id,
            "merchant_name": merchant.name,
            "category": listing.category,
            "amount": order.amount,
            "items": listing.items,  # 盲盒 paid 后揭晓
            "pickup_code": order.pickup_code,
            "qr_content": order.qr_content,
            "pickup_start": iso(listing.pickup_start),
            "pickup_end": iso(listing.pickup_end),
            "match_reason": order.match_reason,
            "match_reason_text": humanize_match_reason(order.match_reason),
        }, user_id=order.user_id)
    db.commit()
    return {"result": "success", "first_effect": ok}


def _handle_refund(db, payload: dict) -> dict:
    order = _find_order(db, payload)
    if not order:
        db.commit()
        return {"result": "success", "first_effect": False}

    ok = conditional_transition(
        db, Order, order.id, "paid", "refunded",
        refund_amount=int(payload.get("refund_amount", order.amount)),
        refund_reason=payload.get("reason"),
    )
    if ok:
        db.refresh(order)
        c = db.get(Commission, order.commission_id)
        # 回退记账 floor 0，仅首次成功同事务执行；终态委托单不复活
        c.spent_amount_month = max(0, c.spent_amount_month - order.amount)
        c.used_count_week = max(0, c.used_count_week - 1)
        from app.modules.listing import inventory

        inventory.release_one(db, order.listing_id)  # 退款释放占份（下架余量仅回落锁定数）
        merchant = db.get(Merchant, order.merchant_id)
        push_card(db, "refund_notice", {
            "order_id": order.id,
            "commission_id": c.id,
            "merchant_name": merchant.name,
            "refund_amount": order.refund_amount,
            "reason": order.refund_reason,
        }, user_id=order.user_id)
    db.commit()
    if ok:
        from app.modules.matching.engine import continue_allocation

        continue_allocation(order.listing_id)
    return {"result": "success", "first_effect": ok}
