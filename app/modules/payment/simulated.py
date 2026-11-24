"""仿真支付：零密钥全流程。写 pay_log(simulated) + 入队 sim_event（虚拟时钟 due），
由 2s job 投递到 notify；仿真确认端点可提前触发，条件更新保证只生效一次。
auto_pay/refund 入队异步通知；instant_pay 返回 pay_url 等用户点击 sim-pay-confirm。
"""
import json
import threading
import uuid
from datetime import timedelta

from app.config import settings
from app.core.clock import clock
from app.models import Commission, PayLog, SimEvent


class SimulatedAdapter:
    def __init__(self):
        self._fail_lock = threading.Lock()
        self._fail_next = 0  # 演示：接下来 n 次 auto_pay 抛错（失败释放续配）

    def set_fail_next(self, n: int):
        with self._fail_lock:
            self._fail_next = max(0, int(n))

    def _log(self, db, type: str, request: dict, response: dict,
             commission_id: int | None = None, order_id: int | None = None):
        db.add(PayLog(
            type=type, commission_id=commission_id, order_id=order_id,
            request_body=json.dumps(request, ensure_ascii=False),
            response_body=json.dumps(response, ensure_ascii=False),
            simulated=True,
        ))

    def _enqueue(self, db, kind: str, payload: dict, delay_seconds: int):
        db.add(SimEvent(
            kind=kind, payload=payload,
            due_at=clock.now() + timedelta(seconds=delay_seconds),
        ))

    def authorize(self, db, commission) -> dict:
        agreement_no = f"SIMAGR{commission.id:06d}{uuid.uuid4().hex[:8]}"
        req = {
            "commission_id": commission.id,
            "per_order_max": commission.per_order_max,
            "weekly_count_max": commission.weekly_count_max,
            "monthly_amount_max": commission.monthly_amount_max,
            "valid_to": commission.valid_to.isoformat(),
        }
        resp = {
            "agreement_no": agreement_no,
            "auth_url": f"/auth.html?commission_id={commission.id}",
        }
        self._log(db, "auth", req, resp, commission_id=commission.id)
        self._enqueue(db, "auth_notify", {
            "commission_id": commission.id,
            "agreement_no": agreement_no,
            "success": True,
        }, settings.SIM_AUTH_DELAY)
        db.flush()
        return {"auth_url": resp["auth_url"], "agreement_no": agreement_no, "simulated": True}

    def auto_pay(self, db, order) -> dict:
        with self._fail_lock:
            if self._fail_next > 0:
                self._fail_next -= 1
                raise RuntimeError("模拟支付失败（fail-next 演示）")
        # out_trade_no 已由引擎先写库；此处写 pay_log + 入队 pay_notify
        c = db.get(Commission, order.commission_id)
        trade_no = f"SIMTRADE{uuid.uuid4().hex[:12]}"
        req = {
            "out_trade_no": order.out_trade_no,
            "amount": order.amount,
            "agreement_no": c.auth_agreement_no if c else None,
        }
        resp = {"alipay_trade_no": trade_no, "success": True}
        self._log(db, "pay", req, resp, commission_id=order.commission_id, order_id=order.id)
        self._enqueue(db, "pay_notify", {
            "order_id": order.id,
            "out_trade_no": order.out_trade_no,
            "alipay_trade_no": trade_no,
            "success": True,
        }, settings.SIM_PAY_DELAY)
        db.flush()
        return {"alipay_trade_no": trade_no, "simulated": True}

    def instant_pay(self, db, order) -> dict:
        # 越界确认后即时付：返回 pay_url + qr_content，等用户点击 sim-pay-confirm
        pay_url = f"/pay.html?order_id={order.id}"
        qr = order.out_trade_no or f"GLEANERS_PAY:{order.id}"
        req = {"out_trade_no": order.out_trade_no, "amount": order.amount}
        resp = {"pay_url": pay_url, "qr_content": qr}
        self._log(db, "pay", req, resp, commission_id=order.commission_id, order_id=order.id)
        db.flush()
        return {"pay_url": pay_url, "qr_content": qr, "simulated": True}

    def refund(self, db, order, amount: int, reason: str) -> dict:
        out_request_no = f"SIMREF{uuid.uuid4().hex[:12]}"  # 独立退款幂等键
        order.out_request_no = out_request_no
        req = {
            "out_trade_no": order.out_trade_no,
            "out_request_no": out_request_no,
            "refund_amount": amount,
            "reason": reason,
        }
        resp = {"success": True, "out_request_no": out_request_no}
        self._log(db, "refund", req, resp, commission_id=order.commission_id, order_id=order.id)
        self._enqueue(db, "refund_notify", {
            "order_id": order.id,
            "out_trade_no": order.out_trade_no,
            "out_request_no": out_request_no,
            "refund_amount": amount,
            "reason": reason,
            "success": True,
        }, settings.SIM_REFUND_DELAY)
        db.flush()
        return {"out_request_no": out_request_no, "simulated": True}

    def query(self, db, order) -> dict:
        return {"out_trade_no": order.out_trade_no, "status": order.status, "simulated": True}
