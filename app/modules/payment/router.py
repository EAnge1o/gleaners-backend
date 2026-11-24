"""支付路由：外部风格回调 + 仿真确认端点（密钥不出服务端）。"""
import json
import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from app import db
from app.core.errors import NotFound, ValidationFailed
from app.core.security import sim_sign
from app.models import Commission, Order, PayLog
from app.modules.payment import notify

router = APIRouter(tags=["payment"])


class SimAuthConfirm(BaseModel):
    commission_id: int


@router.post("/api/payment/sim-auth-confirm")
def sim_auth_confirm(body: SimAuthConfirm):
    """auth.html「同意授权」按钮：服务端构造签名报文内部调 handle。"""
    with db.session_scope() as s:
        c = s.get(Commission, body.commission_id)
        if not c:
            raise NotFound("委托单不存在")
        if c.status == "active":
            return {"result": "success", "already": True}  # 幂等
        if c.status != "pending_auth":
            raise ValidationFailed("委托单不在待授权状态")
        log = (
            s.query(PayLog)
            .filter(PayLog.commission_id == c.id, PayLog.type == "auth")
            .order_by(PayLog.id.desc())
            .first()
        )
        agreement_no = json.loads(log.response_body).get("agreement_no") if log else None
        payload = {
            "commission_id": c.id,
            "agreement_no": agreement_no,
            "success": True,
        }
        raw = {"payload": payload, "sign": sim_sign(payload)}
        return notify.handle(s, "auth", raw)


@router.post("/callback/alipay/auth")
def alipay_auth_callback(raw: dict):
    with db.session_scope() as s:
        return notify.handle(s, "auth", raw)


class SimPayConfirm(BaseModel):
    order_id: int


@router.post("/api/payment/sim-pay-confirm")
def sim_pay_confirm(body: SimPayConfirm):
    """pay.html「确认付款」按钮：服务端构造签名报文内部调 handle。"""
    with db.session_scope() as s:
        order = s.get(Order, body.order_id)
        if not order:
            raise NotFound("订单不存在")
        if order.status == "paid":
            return {"result": "success", "already": True}
        if order.status != "paying":
            raise ValidationFailed("订单不在待支付状态")
        payload = {
            "order_id": order.id,
            "out_trade_no": order.out_trade_no,
            "alipay_trade_no": f"SIMTRADE{uuid.uuid4().hex[:12]}",
            "success": True,
        }
        raw = {"payload": payload, "sign": sim_sign(payload)}
        return notify.handle(s, "pay", raw)


@router.post("/callback/alipay/pay")
def alipay_pay_callback(raw: dict):
    with db.session_scope() as s:
        return notify.handle(s, "pay", raw)


@router.post("/callback/alipay/refund")
def alipay_refund_callback(raw: dict):
    with db.session_scope() as s:
        return notify.handle(s, "refund", raw)
