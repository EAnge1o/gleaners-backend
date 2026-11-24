"""仿真异步通知投递：到期 sim_event → 服务端构造签名报文 → notify.handle。
条件更新 delivered 防止 job 与演示控制台并发重复投递；handle 内部再做业务级幂等。
"""
import logging

from sqlalchemy import update

from app import db
from app.core.clock import clock
from app.core.security import sim_sign
from app.models import SimEvent

log = logging.getLogger(__name__)


def deliver_due():
    now = clock.now()
    with db.session_scope() as s:
        ids = [
            e.id
            for e in s.query(SimEvent)
            .filter(SimEvent.delivered == False, SimEvent.due_at <= now)  # noqa: E712
            .order_by(SimEvent.id)
            .all()
        ]
    for eid in ids:
        with db.session_scope() as s:
            res = s.execute(
                update(SimEvent)
                .where(SimEvent.id == eid, SimEvent.delivered == False)  # noqa: E712
                .values(delivered=True)
            )
            s.commit()
            if res.rowcount == 0:
                continue
            e = s.get(SimEvent, eid)
            kind, payload = e.kind, e.payload
        raw = {"payload": payload, "sign": sim_sign(payload)}
        try:
            with db.session_scope() as s:
                from app.modules.payment import notify

                notify.handle(s, kind.removesuffix("_notify"), raw)
        except Exception:
            log.exception("sim_event %s 投递失败", eid)
