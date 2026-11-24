from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from app.core.clock import clock
from app.db import Base


class PayLog(Base):
    __tablename__ = "pay_log"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, nullable=True, index=True)
    commission_id = Column(Integer, nullable=True, index=True)
    type = Column(String, nullable=False)  # auth/pay/refund/notify
    request_body = Column(Text, nullable=False, default="")
    response_body = Column(Text, nullable=False, default="")
    simulated = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=lambda: clock.now())