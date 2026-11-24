from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text

from app.core.clock import clock
from app.db import Base


class Commission(Base):
    __tablename__ = "commission"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    status = Column(String, nullable=False, default="draft", index=True)
    type = Column(String, nullable=False, default="periodic")  # periodic/single
    raw_text = Column(Text, nullable=False, default="")
    rule_card = Column(JSON, nullable=False, default=dict)

    buy_modes = Column(JSON, nullable=False, default=list)  # blind/public
    categories = Column(JSON, nullable=False, default=list)
    size = Column(String, nullable=False, default="不限")
    merchant_ids = Column(JSON, nullable=False, default=list)

    center_lat = Column(Float, nullable=False, default=0.0)
    center_lng = Column(Float, nullable=False, default=0.0)
    radius_m = Column(Integer, nullable=False, default=1000)

    active_weekdays = Column(JSON, nullable=False, default=list)  # 0=周一
    active_time_start = Column(String, nullable=True)  # "HH:MM"
    active_time_end = Column(String, nullable=True)

    per_order_max = Column(Integer, nullable=False)  # 分
    weekly_count_max = Column(Integer, nullable=True)
    monthly_amount_max = Column(Integer, nullable=True)

    valid_from = Column(DateTime, nullable=False)
    valid_to = Column(DateTime, nullable=False)

    auth_agreement_no = Column(String, nullable=True)
    used_count_week = Column(Integer, nullable=False, default=0)
    spent_amount_month = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, default=lambda: clock.now())
    updated_at = Column(DateTime, default=lambda: clock.now(), onupdate=lambda: clock.now())