"""定时任务集合：全部用 clock.now + 条件更新，可重复执行无副作用。
日/周/月周期任务用 app_meta 记录最近执行周期，run_all_once 反复调用不会重复重置。
释放触发点（确认超时/补单关单）关单后从 phase_cursor 续配。
"""
import logging
from datetime import timedelta

from sqlalchemy import update

from app import db
from app.core.clock import clock
from app.core.state_machine import conditional_transition
from app.models import AppMeta, Commission, Listing, Order, SimEvent, User
from app.modules.feed.service import push_card

log = logging.getLogger(__name__)


def deliver_sim_events():
    from app.modules.payment.sim_events import deliver_due

    deliver_due()


def _continue(listing_id: int):
    from app.modules.matching.engine import continue_allocation

    continue_allocation(listing_id)


def confirm_timeout():
    """30s：pending_confirm 过期 → closed + 释放 + 续配（与 confirm 条件更新裁决竞态）。"""
    now = clock.now()
    with db.session_scope() as s:
        rows = (
            s.query(Order.id, Order.listing_id)
            .filter(Order.status == "pending_confirm", Order.confirm_expires_at <= now)
            .all()
        )
    from app.modules.listing import inventory

    for oid, lid in rows:
        with db.session_scope() as s:
            ok = conditional_transition(s, Order, oid, "pending_confirm", "closed")
            if ok:
                inventory.release_one(s, lid)
            s.commit()
        if ok:
            _continue(lid)


def pay_reconcile():
    """60s：paying>3min → adapter.query 对账；仿真下若无待投递通知则补单关单释放续配。"""
    now = clock.now()
    cutoff = now - timedelta(minutes=3)
    with db.session_scope() as s:
        rows = (
            s.query(Order.id, Order.listing_id)
            .filter(Order.status == "paying", Order.created_at <= cutoff)
            .all()
        )
        pending = {
            e.payload.get("order_id")
            for e in s.query(SimEvent)
            .filter(SimEvent.kind == "pay_notify", SimEvent.delivered == False)  # noqa: E712
            .all()
        }
    from app.modules.listing import inventory
    from app.modules.payment.factory import get_adapter

    for oid, lid in rows:
        ok = False
        with db.session_scope() as s:
            o = s.get(Order, oid)
            if not o or o.status != "paying":
                continue
            get_adapter().query(s, o)
            if oid in pending:
                continue  # 通知仍在队列，等待投递
            ok = conditional_transition(s, Order, oid, "paying", "closed")
            if ok:
                inventory.release_one(s, lid)
            s.commit()
        if ok:
            _continue(lid)


def pickup_reminder():
    """60s：距 pickup_start≤30min 且未提醒的 paid 单 → 推 pickup_reminder 卡 + 标记。"""
    now = clock.now()
    with db.session_scope() as s:
        rows = (
            s.query(Order, Listing)
            .join(Listing, Order.listing_id == Listing.id)
            .filter(
                Order.status == "paid",
                Order.reminder_sent_at.is_(None),
                Listing.pickup_start <= now + timedelta(minutes=30),
                Listing.pickup_end >= now,
            )
            .all()
        )
        data = [
            (o.id, o.user_id, o.pickup_code, o.qr_content, l.category,
             l.pickup_start, l.pickup_end, o.merchant_id)
            for o, l in rows
        ]
    from app.core.utils import iso
    from app.models import Merchant

    for oid, uid, code, qr, category, ps, pe, mid in data:
        with db.session_scope() as s:
            res = s.execute(
                update(Order)
                .where(Order.id == oid, Order.status == "paid",
                       Order.reminder_sent_at.is_(None))
                .values(reminder_sent_at=now)
            )
            if res.rowcount == 0:
                continue
            m = s.get(Merchant, mid)
            push_card(s, "pickup_reminder", {
                "order_id": oid,
                "merchant_name": m.name if m else None,
                "category": category,
                "pickup_code": code,
                "qr_content": qr,
                "pickup_start": iso(ps),
                "pickup_end": iso(pe),
            }, user_id=uid)
            s.commit()


def listing_off_shelf():
    """5min：过 pickup_end 的 on_sale/sold_out → off_shelf。
    关未付单释放占份；paid 单留给 no_show_sweep（不退款不释放，份额已消耗）。"""
    now = clock.now()
    with db.session_scope() as s:
        ids = [
            l.id for l in s.query(Listing)
            .filter(Listing.status.in_(["on_sale", "sold_out"]), Listing.pickup_end < now)
            .all()
        ]
    from app.modules.listing import inventory

    for lid in ids:
        with db.session_scope() as s:
            l = s.get(Listing, lid)
            if not l or l.status == "off_shelf":
                continue
            orders = (
                s.query(Order)
                .filter(Order.listing_id == lid,
                        Order.status.in_(["pending_confirm", "paying"]))
                .all()
            )
            for o in orders:
                if conditional_transition(s, Order, o.id, o.status, "closed"):
                    inventory.release_one(s, lid)
            l.qty_available = 0
            conditional_transition(s, Listing, lid, l.status, "off_shelf")
            s.commit()


def no_show_sweep():
    """5min：paid 过 pickup_end → no_show + 信用−20（不退款不释放库存）。"""
    now = clock.now()
    with db.session_scope() as s:
        rows = (
            s.query(Order.id, Order.user_id, Order.listing_id)
            .join(Listing, Order.listing_id == Listing.id)
            .filter(Order.status == "paid", Listing.pickup_end < now)
            .all()
        )
    from app.modules.listing import inventory
    from app.modules.pickup import credit

    for oid, uid, lid in rows:
        with db.session_scope() as s:
            ok = conditional_transition(s, Order, oid, "paid", "no_show")
            if ok:
                inventory.consume_one(s, lid)
                credit.on_no_show(s, s.get(User, uid), oid)
            s.commit()


def commission_expire():
    """每天 0 点：过 valid_to 的非终态委托 → expired（状态机天然幂等）。"""
    now = clock.now()
    with db.session_scope() as s:
        rows = (
            s.query(Commission.id, Commission.status)
            .filter(
                Commission.status.in_(["draft", "pending_auth", "active", "paused", "suspended"]),
                Commission.valid_to < now,
            )
            .all()
        )
        for cid, st in rows:
            conditional_transition(s, Commission, cid, st, "expired")
        s.commit()


def _periodic(meta_key: str, period_id: str, fn):
    """周期守卫：首次仅记录基线周期（不重置，避免误清当期已累计额度）；
    周期变化才执行 fn；同一周期内重复调用跳过，保证 run_all_once 可反复执行。"""
    with db.session_scope() as s:
        m = s.get(AppMeta, meta_key)
        if m is None:
            s.add(AppMeta(key=meta_key, value=period_id))
            s.commit()
            return
        if m.value == period_id:
            return
        fn(s)
        m.value = period_id
        s.commit()


def weekly_reset():
    """周一 0 点：used_count_week=0（周次数用完仍 active，reset 自然恢复）。"""
    now = clock.now()
    iso = now.isocalendar()
    _periodic(
        "weekly_reset", f"{iso[0]}-W{iso[1]}",
        lambda s: s.query(Commission).filter(Commission.used_count_week > 0)
        .update({"used_count_week": 0}),
    )


def monthly_reset():
    """每月 1 日 0 点：spent_amount_month=0。"""
    now = clock.now()
    _periodic(
        "monthly_reset", now.strftime("%Y-%m"),
        lambda s: s.query(Commission).filter(Commission.spent_amount_month > 0)
        .update({"spent_amount_month": 0}),
    )


def win_count_recompute():
    """每天 0 点：滑窗近 7 天 picked_up 计数重算 win_count_7d。"""
    now = clock.now()
    cutoff = now - timedelta(days=7)

    def _run(s):
        for u in s.query(User).all():
            u.win_count_7d = (
                s.query(Order.id)
                .filter(Order.user_id == u.id, Order.status == "picked_up",
                        Order.picked_up_at >= cutoff)
                .count()
            )

    _periodic("win_count_recompute", now.strftime("%Y-%m-%d"), _run)


def register(sched):
    sched.add_job(deliver_sim_events, "interval", seconds=2, id="deliver_sim_events")
    sched.add_job(confirm_timeout, "interval", seconds=30, id="confirm_timeout")
    sched.add_job(pay_reconcile, "interval", seconds=60, id="pay_reconcile")
    sched.add_job(pickup_reminder, "interval", seconds=60, id="pickup_reminder")
    sched.add_job(no_show_sweep, "interval", seconds=300, id="no_show_sweep")
    sched.add_job(listing_off_shelf, "interval", seconds=300, id="listing_off_shelf")
    sched.add_job(commission_expire, "cron", hour=0, minute=0, id="commission_expire")
    sched.add_job(weekly_reset, "cron", day_of_week="mon", hour=0, minute=0, id="weekly_reset")
    sched.add_job(monthly_reset, "cron", day=1, hour=0, minute=0, id="monthly_reset")
    sched.add_job(win_count_recompute, "cron", hour=0, minute=5, id="win_count_recompute")


def run_all_once():
    """演示控制台快进后立即执行全部 job（顺序：投递→超时→对账→提醒→下架→未取→周期）。"""
    deliver_sim_events()
    confirm_timeout()
    pay_reconcile()
    pickup_reminder()
    listing_off_shelf()
    no_show_sweep()
    commission_expire()
    weekly_reset()
    monthly_reset()
    win_count_recompute()