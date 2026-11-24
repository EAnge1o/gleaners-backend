"""信用副作用：仅在 picked_up/no_show 条件更新首次成功后同事务调用。
on_picked_up +1(cap100)/streak=0/win_count_7d+1；
on_no_show −20(floor0)/streak+1，streak≥3 → 该用户全部 active/paused 委托 suspended + 推卡。
CreditLog 记录实际生效增量（cap/floor 后），保证审计与账面一致。
"""
from app.core.state_machine import conditional_transition
from app.models import Commission, CreditLog, User
from app.modules.feed.service import push_card

CREDIT_CAP = 100
NO_SHOW_PENALTY = 20
SUSPEND_STREAK = 3


def on_picked_up(db, user: User, order_id: int):
    old = user.credit_score
    user.credit_score = min(CREDIT_CAP, old + 1)
    user.no_show_streak = 0
    user.win_count_7d += 1
    db.add(CreditLog(user_id=user.id, order_id=order_id,
                     delta=user.credit_score - old, reason="picked_up"))
    db.flush()


def on_no_show(db, user: User, order_id: int):
    old = user.credit_score
    user.credit_score = max(0, old - NO_SHOW_PENALTY)
    user.no_show_streak += 1
    db.add(CreditLog(user_id=user.id, order_id=order_id,
                     delta=user.credit_score - old, reason="no_show"))
    if user.no_show_streak >= SUSPEND_STREAK:
        rows = (
            db.query(Commission)
            .filter(Commission.user_id == user.id,
                    Commission.status.in_(["active", "paused"]))
            .all()
        )
        for c in rows:
            if conditional_transition(db, Commission, c.id, c.status, "suspended"):
                push_card(db, "commission_notice", {
                    "commission_id": c.id,
                    "status": "suspended",
                    "reason": f"连续{user.no_show_streak}次未取货，委托已暂停，需重新授权恢复",
                }, user_id=user.id)
    db.flush()