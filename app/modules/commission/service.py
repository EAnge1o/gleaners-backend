"""委托单生命周期：草稿→授权→生效→暂停/恢复/取消→完成。状态变更一律经状态机。"""
from datetime import timedelta

from app.core.clock import clock
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.state_machine import conditional_transition, set_status
from app.core.utils import iso
from app.models import Commission, Order, User
from app.modules.agent.schemas import RuleCard, validate_or_raise
from app.modules.feed.service import push_card
from app.modules.payment.factory import get_adapter


def _owned(db, user, cid: int) -> Commission:
    c = db.get(Commission, cid)
    if not c:
        raise NotFound("委托单不存在")
    if c.user_id != user.id:
        raise Forbidden("无权操作他人委托单")
    return c


def to_dict(c: Commission) -> dict:
    return {
        "id": c.id,
        "status": c.status,
        "type": c.type,
        "raw_text": c.raw_text,
        "rule_card": c.rule_card,
        "buy_modes": c.buy_modes,
        "categories": c.categories,
        "size": c.size,
        "merchant_ids": c.merchant_ids,
        "center_lat": c.center_lat,
        "center_lng": c.center_lng,
        "radius_m": c.radius_m,
        "active_weekdays": c.active_weekdays,
        "active_time_start": c.active_time_start,
        "active_time_end": c.active_time_end,
        "per_order_max": c.per_order_max,
        "weekly_count_max": c.weekly_count_max,
        "monthly_amount_max": c.monthly_amount_max,
        "valid_from": iso(c.valid_from),
        "valid_to": iso(c.valid_to),
        "auth_agreement_no": c.auth_agreement_no,
        "used_count_week": c.used_count_week,
        "spent_amount_month": c.spent_amount_month,
        "created_at": iso(c.created_at),
    }


def _apply_card(c: Commission, card: RuleCard):
    c.type = card.type
    c.buy_modes = card.buy_modes
    c.categories = card.categories
    c.size = card.size
    c.merchant_ids = card.merchant_ids
    c.radius_m = card.radius_m
    c.active_weekdays = card.active_weekdays
    c.active_time_start = card.active_time_start
    c.active_time_end = card.active_time_end
    c.per_order_max = card.per_order_max
    c.weekly_count_max = card.weekly_count_max
    c.monthly_amount_max = card.monthly_amount_max


def create_draft(db, user, text: str, card: RuleCard) -> Commission:
    now = clock.now()
    c = Commission(
        user_id=user.id,
        status="draft",
        raw_text=text,
        rule_card=card.model_dump(),
        center_lat=user.home_lat,
        center_lng=user.home_lng,
        valid_from=now,
        valid_to=now + timedelta(days=card.valid_days),
    )
    _apply_card(c, card)
    db.add(c)
    db.flush()
    return c


def patch_draft(db, user, cid: int, patch: dict) -> dict:
    c = _owned(db, user, cid)
    if c.status != "draft":
        raise Conflict("仅草稿状态可修改规则")
    card = validate_or_raise(RuleCard, {**c.rule_card, **patch})
    _apply_card(c, card)
    c.rule_card = card.model_dump()
    if "valid_days" in patch:
        c.valid_to = c.valid_from + timedelta(days=card.valid_days)
    db.commit()
    db.refresh(c)
    return to_dict(c)


def list_mine(db, user, status: str | None = None) -> list[dict]:
    q = db.query(Commission).filter(Commission.user_id == user.id)
    if status:
        q = q.filter(Commission.status == status)
    return [to_dict(c) for c in q.order_by(Commission.id.desc()).all()]


def get_mine(db, user, cid: int) -> dict:
    return to_dict(_owned(db, user, cid))


def authorize(db, user, cid: int) -> dict:
    """draft/suspended → pending_auth → 适配层发起授权（仿真写 pay_log + sim_event）。"""
    c = _owned(db, user, cid)
    if c.status not in ("draft", "suspended"):
        raise Conflict(f"状态 {c.status} 不可发起授权")
    set_status(db, c, "pending_auth", "commission")
    res = get_adapter().authorize(db, c)
    db.commit()
    db.refresh(c)
    return {"commission": to_dict(c), **res}


def on_auth_notify(db, payload: dict) -> bool:
    """授权回调副作用（仅首次生效）：成功→active 存协议号+streak 清零；失败→cancelled。"""
    cid = int(payload["commission_id"])
    c = db.get(Commission, cid)
    if not c:
        return False
    success = bool(payload.get("success", True))
    if success:
        ok = conditional_transition(
            db, Commission, cid, "pending_auth", "active",
            auth_agreement_no=payload.get("agreement_no"),
        )
        if ok:
            u = db.get(User, c.user_id)
            u.no_show_streak = 0  # 重新授权成功清零连续未取货
            push_card(db, "auth_result", {
                "commission_id": cid,
                "success": True,
                "agreement_no": payload.get("agreement_no"),
                "rule_card": c.rule_card,
            }, user_id=c.user_id)
    else:
        ok = conditional_transition(db, Commission, cid, "pending_auth", "cancelled")
        if ok:
            push_card(db, "auth_result", {
                "commission_id": cid, "success": False, "rule_card": c.rule_card,
            }, user_id=c.user_id)
    return ok


def pause(db, user, cid: int) -> dict:
    c = _owned(db, user, cid)
    set_status(db, c, "paused", "commission")
    db.commit()
    db.refresh(c)
    return to_dict(c)


def resume(db, user, cid: int) -> dict:
    """paused 直接恢复；suspended 必须重新授权（无直达 active 边）。"""
    c = _owned(db, user, cid)
    if c.status == "paused":
        set_status(db, c, "active", "commission")
        db.commit()
        db.refresh(c)
        return {"commission": to_dict(c)}
    if c.status == "suspended":
        set_status(db, c, "pending_auth", "commission")
        res = get_adapter().authorize(db, c)
        db.commit()
        db.refresh(c)
        return {"commission": to_dict(c), **res}
    raise Conflict(f"状态 {c.status} 不可恢复")


def cancel(db, user, cid: int) -> dict:
    c = _owned(db, user, cid)
    set_status(db, c, "cancelled", "commission")
    db.commit()
    db.refresh(c)
    return to_dict(c)


def check_completion(db, c: Commission):
    """single 成交→completed；periodic 月额度耗尽→completed；周次数用完不改状态。
    与业务写操作同事务，调用方负责 commit。"""
    if c.status not in ("active", "paused"):
        return
    if c.type == "single":
        hit = (
            db.query(Order.id)
            .filter(Order.commission_id == c.id, Order.status.in_(["paid", "picked_up"]))
            .first()
        )
        if hit:
            set_status(db, c, "completed", "commission")
            push_card(db, "commission_notice", {
                "commission_id": c.id, "status": "completed", "reason": "单次委托已成交",
            }, user_id=c.user_id)
            return
    if (
        c.type == "periodic"
        and c.monthly_amount_max is not None
        and c.spent_amount_month >= c.monthly_amount_max
    ):
        set_status(db, c, "completed", "commission")
        push_card(db, "commission_notice", {
            "commission_id": c.id, "status": "completed", "reason": "本月额度已用完",
        }, user_id=c.user_id)


def quota(db, user) -> dict:
    rows = (
        db.query(Commission)
        .filter(
            Commission.user_id == user.id,
            Commission.status.in_(["active", "paused", "suspended"]),
        )
        .order_by(Commission.id)
        .all()
    )
    return {
        "items": [
            {
                "commission_id": c.id,
                "status": c.status,
                "weekly": {"used": c.used_count_week, "max": c.weekly_count_max},
                "monthly": {"used": c.spent_amount_month, "max": c.monthly_amount_max},
            }
            for c in rows
        ]
    }
