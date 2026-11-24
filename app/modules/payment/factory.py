from app.config import settings
from app.modules.payment.simulated import SimulatedAdapter

_sim = SimulatedAdapter()


def get_adapter():
    """PAY_MODE=simulate|sandbox|agent_pay；真实渠道无凭证自动降级仿真。"""
    if settings.PAY_MODE == "simulate":
        return _sim
    if not (settings.ALIPAY_APP_ID and settings.ALIPAY_PRIVATE_KEY):
        return _sim
    raise NotImplementedError("真实沙箱适配器于 P5 接入（当前请保持 PAY_MODE=simulate）")
