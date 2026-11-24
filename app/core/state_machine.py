from sqlalchemy import update

from app.core.errors import IllegalTransition

# 0=周一 ... 6=周日（与 datetime.weekday() 一致）
COMMISSION_TRANSITIONS = {
    "draft": {"pending_auth", "cancelled", "expired"},
    "pending_auth": {"active", "cancelled", "expired"},
    "active": {"paused", "suspended", "completed", "expired", "cancelled"},
    "paused": {"active", "suspended", "completed", "expired", "cancelled"},
    "suspended": {"pending_auth", "cancelled", "expired"},  # 恢复必须重新授权
    "completed": set(),
    "expired": set(),
    "cancelled": set(),
}

LISTING_TRANSITIONS = {
    "on_sale": {"sold_out", "off_shelf"},
    "sold_out": {"on_sale", "off_shelf"},
    "off_shelf": set(),
}

ORDER_TRANSITIONS = {
    "pending_confirm": {"paying", "closed"},
    "paying": {"paid", "closed"},
    "paid": {"picked_up", "no_show", "refunded"},
    "picked_up": set(),
    "no_show": set(),
    "refunded": set(),
    "closed": set(),
}

TABLES = {
    "commission": COMMISSION_TRANSITIONS,
    "listing": LISTING_TRANSITIONS,
    "order": ORDER_TRANSITIONS,
}


def can_transition(table: str, cur: str, target: str) -> bool:
    return target in TABLES[table].get(cur, set())


def set_status(db, obj, target: str, table: str, **extra):
    """进程内状态变更唯一入口，非法转移抛 IllegalTransition。"""
    cur = obj.status
    if not can_transition(table, cur, target):
        raise IllegalTransition(f"{table} 状态 {cur} -> {target} 非法")
    obj.status = target
    for k, v in extra.items():
        setattr(obj, k, v)
    db.flush()
    return obj


def conditional_transition(db, model, obj_id: int, from_status: str, to_status: str, **extra) -> bool:
    """并发安全的条件更新：UPDATE ... WHERE id AND status=from_status。
    rowcount=0 表示已被他人转移（重复/竞态），调用方据此放弃。"""
    values = {"status": to_status, **extra}
    stmt = (
        update(model)
        .where(model.id == obj_id, model.status == from_status)
        .values(**values)
    )
    res = db.execute(stmt)
    db.flush()
    return res.rowcount > 0