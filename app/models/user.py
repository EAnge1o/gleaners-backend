from sqlalchemy import Column, DateTime, Float, Integer, JSON, String

from app.core.clock import clock
from app.db import Base


class User(Base):
    __tablename__ = "user"

    id = Column(Integer, primary_key=True)
    alipay_user_id = Column(String, unique=True, nullable=True)
    nickname = Column(String, nullable=False)
    home_lat = Column(Float, nullable=False, default=0.0)
    home_lng = Column(Float, nullable=False, default=0.0)
    allergens = Column(JSON, nullable=False, default=list)
    diet_tags = Column(JSON, nullable=False, default=list)
    wallet_single_limit = Column(Integer, nullable=False, default=1500)  # 分
    credit_score = Column(Integer, nullable=False, default=100)
    no_show_streak = Column(Integer, nullable=False, default=0)
    win_count_7d = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: clock.now())