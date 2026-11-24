"""取货码生成（6 位 SystemRandom 查重）+ 商家核销。
verify 校验商家归属+时间窗+status=paid → 条件更新 picked_up（重复幂等返回首次结果）→ 信用。
"""
from app.core.clock import clock
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.state_machine import conditional_transition
from app.core.utils import gen_pickup_code
from app.models import Listing, Order, User
from app.modules.pickup import credit


def generate_pickup_code(db) -> str:
    for _ in range(20):
        code = gen_pickup_code()
        exists = (
            db.query(Order.id)
            .filter(Order.pickup_code == code, Order.status.in_(["paid", "picked_up"]))
            .first()
        )
        if not exists:
            return code
    return gen_pickup_code()


def qr_content(order_id: int, code: str) -> str:
    return f"GLEANERS:{order_id}:{code}"


def verify(db, merchant, order_id: int | None = None, code: str | None = None) -> dict:
    from app.modules.listing import inventory
    from app.modules.order.service import to_dict as order_to_dict

    if order_id is not None:
        o = db.get(Order, order_id)
        if not o:
            raise NotFound("订单不存在")
    elif code:
        o = (
            db.query(Order)
            .filter(Order.pickup_code == code, Order.status.in_(["paid", "picked_up"]))
            .first()
        )
        if not o:
            raise NotFound("取货码无效")
    else:
        raise NotFound("缺少订单号或取货码")

    if o.merchant_id != merchant.id:
        raise Forbidden("无权核销他人订单")
    if o.status == "picked_up":
        return {"order": order_to_dict(db, o), "already": True}  # 幂等：返回首次结果
    if o.status != "paid":
        raise Conflict(f"订单状态 {o.status} 不可核销")

    l = db.get(Listing, o.listing_id)
    now = clock.now()
    if not (l.pickup_start <= now <= l.pickup_end):
        raise Conflict("不在取货时间窗内")

    ok = conditional_transition(db, Order, o.id, "paid", "picked_up", picked_up_at=now)
    if ok:
        db.refresh(o)
        inventory.consume_one(db, o.listing_id)  # 份额已消耗，锁定数回落（不回补可用）
        credit.on_picked_up(db, db.get(User, o.user_id), o.id)
    db.commit()
    db.refresh(o)
    return {"order": order_to_dict(db, o), "already": not ok}