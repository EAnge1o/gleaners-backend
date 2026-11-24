from sqlalchemy import Column, DateTime, Float, Integer, String

from app.core.clock import clock
from app.db import Base


class Merchant(Base):
    __tablename__ = "merchant"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    address = Column(String, nullable=False, default="")
    lat = Column(Float, nullable=False, default=0.0)
    lng = Column(Float, nullable=False, default=0.0)
    rating = Column(Float, nullable=False, default=5.0)
    rating_count = Column(Integer, nullable=False, default=0)
    pay_channel = Column(String, nullable=False, default="sandbox")  # sandbox/payment_link/prepay
    pay_account_info = Column(String, nullable=True)  # 加密存储
    created_at = Column(DateTime, default=lambda: clock.now())