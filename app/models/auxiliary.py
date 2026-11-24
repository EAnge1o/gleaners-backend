from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)

from app.core.clock import clock
from app.db import Base


class FeedCard(Base):
    __tablename__ = "feed_card"

    id = Column(Integer, primary_key=True, autoincrement=True)  # 自增即游标
    user_id = Column(Integer, nullable=True, index=True)
    merchant_id = Column(Integer, nullable=True, index=True)
    type = Column(String, nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=lambda: clock.now())


class MatchRound(Base):
    __tablename__ = "match_round"

    id = Column(Integer, primary_key=True)
    listing_id = Column(Integer, ForeignKey("listing.id"), nullable=False, index=True)
    seed = Column(String, nullable=False)
    phase_cursor = Column(Integer, nullable=False, default=0)
    status = Column(String, nullable=False, default="open")  # open/exhausted
    created_at = Column(DateTime, default=lambda: clock.now())


class MatchCandidate(Base):
    __tablename__ = "match_candidate"

    id = Column(Integer, primary_key=True)
    round_id = Column(Integer, ForeignKey("match_round.id"), nullable=False, index=True)
    rank = Column(Integer, nullable=False)
    commission_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    in_range = Column(Boolean, nullable=False)
    weight = Column(Float, nullable=False)
    factors = Column(JSON, nullable=False, default=dict)
    overrun_reasons = Column(JSON, nullable=False, default=list)
    allocated_order_id = Column(Integer, nullable=True)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_record"
    __table_args__ = (UniqueConstraint("endpoint", "key", name="uq_idem_endpoint_key"),)

    id = Column(Integer, primary_key=True)
    endpoint = Column(String, nullable=False)
    key = Column(String, nullable=False)
    request_hash = Column(String, nullable=False)
    status = Column(String, nullable=False, default="processing")
    response_status = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: clock.now())


class SimEvent(Base):
    __tablename__ = "sim_event"

    id = Column(Integer, primary_key=True)
    kind = Column(String, nullable=False)  # auth_notify/pay_notify/refund_notify
    payload = Column(JSON, nullable=False, default=dict)
    due_at = Column(DateTime, nullable=False, index=True)
    delivered = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=lambda: clock.now())


class AppMeta(Base):
    __tablename__ = "app_meta"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=False, default="")