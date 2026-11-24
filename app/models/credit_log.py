from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.core.clock import clock
from app.db import Base


class CreditLog(Base):
    __tablename__ = "credit_log"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    order_id = Column(Integer, nullable=True)
    delta = Column(Integer, nullable=False)
    reason = Column(String, nullable=False)  # no_show/picked_up
    created_at = Column(DateTime, default=lambda: clock.now())