from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String

from app.core.clock import clock
from app.db import Base


class Order(Base):
    __tablename__ = "order"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    commission_id = Column(Integer, ForeignKey("commission.id"), nullable=False, index=True)
    listing_id = Column(Integer, ForeignKey("listing.id"), nullable=False, index=True)
    merchant_id = Column(Integer, ForeignKey("merchant.id"), nullable=False, index=True)

    amount = Column(Integer, nullable=False)  # 分
    status = Column(String, nullable=False, default="pending_confirm", index=True)
    pay_mode = Column(String, nullable=False)  # auto/instant

    out_trade_no = Column(String, unique=True, nullable=True)
    alipay_trade_no = Column(String, nullable=True)
    out_request_no = Column(String, nullable=True)  # 退款幂等键

    overrun_reasons = Column(JSON, nullable=False, default=list)
    confirm_expires_at = Column(DateTime, nullable=True)
    match_reason = Column(JSON, nullable=True)
    pickup_code = Column(String, nullable=True)
    qr_content = Column(String, nullable=True)

    paid_at = Column(DateTime, nullable=True)
    picked_up_at = Column(DateTime, nullable=True)
    refund_amount = Column(Integer, nullable=False, default=0)
    refund_reason = Column(String, nullable=True)
    reminder_sent_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=lambda: clock.now())
    updated_at = Column(DateTime, default=lambda: clock.now(), onupdate=lambda: clock.now())