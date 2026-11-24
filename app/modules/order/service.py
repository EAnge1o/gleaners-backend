"""订单查询 + 越界确认/拒绝。盲盒 items 仅 paid 后（paid_at 非空）可见，未支付不泄露。
confirm/reject 用条件更新裁决与超时 job 的竞态；释放后从 phase_cursor 续配。
"""
import json

from app.core.clock import clock
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.state_machine import conditional_transition
from app.core.utils import iso
from app.models import Listing, Merchant, Order, PayLog
from app.modules.listing import inventory
from app.modules.matching.engine import humanize_match_reason
from app.modules.payment.factory import get_adapter


def to_dict(db, o: Order) -> dict:
    l = db.get(Listing, o.listing_id)
    m = db.get(Merchant, o.merchant_id)
    paid = o.paid_at is not None
    return {
        "id": o.id,
        "user_id": o.user_id,
        "commission_id": o.commission_id,
        "listing_id": o.listing_id,
        "merchant_id": o.merchant_id,
        "merchant_name": m.name if m else None,
        "category": l.category if l else None,
        "amount": o.amount,
        "status": o.status,
        "pay_mode": o.pay_mode,
        "overrun_reasons": o.overrun_reasons,
        "confirm_expires_at": iso(o.confirm_expires_at),
        "match_reason": o.match_reason,
        "match_reason_text": humanize_match_reason(o.match_reason),
        "items": l.items if (l and paid) else None,
        "pickup_code": o.pickup_code if paid else None,
        "qr_content": o.qr_content if paid else None,
        "pickup_start": iso(l.pickup_start) if l else None,
        "pickup_end": iso(l.pickup_end) if l else None,
        "paid_at": iso(o.paid_at),
        "picked_up_at": iso(o.picked_up_at),
        "refund_amount": o.refund_amount,
        "refund_reason": o.refund_reason,
        "created_at": iso(o.created_at),
    }


def get_mine(db, user, oid: int) -> dict:
    o = db.get(Order, oid)
    if not o:
        raise NotFound("订单不存在")
    if o.user_id != user.id:
        raise Forbidden("无权查看他人订单")
    return to_dict(db, o)


def list_mine(db, user, status: str | None = None) -> list[dict]:
    q = db.query(Order).filter(Order.user_id == user.id)
    if status:
        q = q.filter(Order.status == status)
    return [to_dict(db, o) for o in q.order_by(Order.id.desc()).all()]


def list_merchant(db, merchant, status: str | None = None) -> list[dict]:
    q = db.query(Order).filter(Order.merchant_id == merchant.id)
    if status:
        q = q.filter(Order.status == status)
    return [to_dict(db, o) for o in q.order_by(Order.id.desc()).all()]


def _owned(db, user, oid: int) -> Order:
    o = db.get(Order, oid)
    if not o:
        raise NotFound("订单不存在")
    if o.user_id != user.id:
        raise Forbidden("无权操作他人订单")
    return o


def _last_pay_entry(db, o: Order) -> dict:
    """重复确认时回放最近一次 instant_pay 响应（不重复写 pay_log）。"""
    log = (
        db.query(PayLog)
        .filter(PayLog.order_id == o.id, PayLog.type == "pay")
        .order_by(PayLog.id.desc())
        .first()
    )
    return json.loads(log.response_body) if log and log.response_body else {}


def _close_and_release(db, o: Order) -> bool:
    ok = conditional_transition(db, Order, o.id, "pending_confirm", "closed")
    if ok:
        inventory.release_one(db, o.listing_id)
    db.commit()
    if ok:
        from app.modules.matching.engine import continue_allocation

        continue_allocation(o.listing_id)
    return ok


def confirm(db, user, oid: int) -> dict:
    """越界确认：pending_confirm→paying 条件更新裁决与超时 job 竞态；
    已过期先关单释放续配再拒绝；重复确认幂等回放支付入口。"""
    o = _owned(db, user, oid)
    if o.status == "paying" and o.pay_mode == "instant":
        return {"order": to_dict(db, o), **_last_pay_entry(db, o), "already": True}
    if o.status == "closed":
        raise Conflict("订单已关闭（确认超时或已拒绝）")
    if o.status != "pending_confirm":
        raise Conflict(f"订单状态 {o.status} 不可确认")
    now = clock.now()
    if o.confirm_expires_at and now >= o.confirm_expires_at:
        _close_and_release(db, o)
        raise Conflict("确认已超时，订单已关闭")
    ok = conditional_transition(db, Order, o.id, "pending_confirm", "paying")
    if not ok:
        db.refresh(o)
        if o.status == "paying":  # 竞态中已被确认 → 幂等回放
            return {"order": to_dict(db, o), **_last_pay_entry(db, o), "already": True}
        raise Conflict("订单已被处理（超时或拒绝）")
    db.refresh(o)
    res = get_adapter().instant_pay(db, o)
    db.commit()
    db.refresh(o)
    return {"order": to_dict(db, o), **res}


def reject(db, user, oid: int) -> dict:
    """越界拒绝：关单释放占份并续配；重复拒绝幂等。"""
    o = _owned(db, user, oid)
    if o.status == "closed":
        return {"order": to_dict(db, o), "already": True}
    if o.status != "pending_confirm":
        raise Conflict(f"订单状态 {o.status} 不可拒绝")
    ok = _close_and_release(db, o)
    db.refresh(o)
    if not ok and o.status != "closed":
        raise Conflict("订单已被处理")
    return {"order": to_dict(db, o)}