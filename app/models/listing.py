from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String

from app.core.clock import clock
from app.db import Base


class Listing(Base):
    __tablename__ = "listing"

    id = Column(Integer, primary_key=True)
    merchant_id = Column(Integer, ForeignKey("merchant.id"), nullable=False, index=True)
    status = Column(String, nullable=False, default="on_sale", index=True)
    mode = Column(String, nullable=False)  # blind/public
    category = Column(String, nullable=False)
    size = Column(String, nullable=False, default="标准")
    diet_tags = Column(JSON, nullable=False, default=list)
    items = Column(JSON, nullable=False, default=list)
    allergens = Column(JSON, nullable=False, default=list)  # 盲盒可为 ["未知"]

    original_price = Column(Integer, nullable=False)  # 分
    price = Column(Integer, nullable=False)  # 分
    qty_total = Column(Integer, nullable=False)
    qty_available = Column(Integer, nullable=False)
    qty_locked = Column(Integer, nullable=False, default=0)

    pickup_start = Column(DateTime, nullable=False)
    pickup_end = Column(DateTime, nullable=False)
    produced_at = Column(DateTime, nullable=True)

    current_round_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: clock.now())