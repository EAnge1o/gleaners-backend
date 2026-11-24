"""支付适配层协议：simulate / sandbox / agent_pay 一键切换，业务代码只依赖本协议。"""
from typing import Protocol


class PaymentAdapter(Protocol):
    def authorize(self, db, commission) -> dict:
        """发起协议授权，返回 auth_url 等；异步结果走 auth_notify 回调。"""
        ...

    def auto_pay(self, db, order) -> dict:
        """范围内自动扣款；异步结果走 pay_notify 回调。"""
        ...

    def instant_pay(self, db, order) -> dict:
        """越界确认后即时付，返回 pay_url + qr_content。"""
        ...

    def refund(self, db, order, amount: int, reason: str) -> dict:
        """退款（独立 out_request_no 幂等）；异步结果走 refund_notify 回调。"""
        ...

    def query(self, db, order) -> dict:
        """对账查询：paying 超时单主动查支付结果。"""
        ...
