"""演示控制台 API（仅 ENV=dev，由 main.py 条件挂载）：
种子重置 / 时间快进 / 任务触发 / fail-next / 全表状态 dump / 公平性快照 / 一键四场景。
"""
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app import db
from app.core.clock import clock
from app.core.errors import NotFound
from app.core.utils import iso
from app.models import (
    AppMeta,
    Commission,
    CreditLog,
    FeedCard,
    IdempotencyRecord,
    Listing,
    MatchCandidate,
    MatchRound,
    Merchant,
    Order,
    PayLog,
    SimEvent,
    User,
)
from app.modules.demo import scenarios
from app.modules.matching import snapshot
from app.modules.payment.factory import get_adapter
from app.modules.scheduler.registry import run_all_jobs_once
from app.seed import seed

router = APIRouter(prefix="/api/demo", tags=["demo"])


class FastForward(BaseModel):
    seconds: int = Field(ge=1, le=30 * 86400)


class FailNext(BaseModel):
    n: int = Field(ge=0, le=100)


def _now() -> dict:
    return {"server_now": iso(clock.now()), "clock_offset": clock.offset}


@router.post("/seed")
def reseed():
    seed()
    return {"ok": True, **_now()}


@router.post("/time/fast-forward")
def fast_forward(body: FastForward):
    clock.advance(body.seconds)
    run_all_jobs_once()  # 快进=拨钟+立即执行全部任务（含仿真通知投递）
    return {"ok": True, "advanced": body.seconds, **_now()}


@router.post("/time/reset")
def time_reset():
    clock.reset()
    return {"ok": True, **_now()}


@router.post("/jobs/run")
def jobs_run():
    run_all_jobs_once()
    return {"ok": True, **_now()}


@router.post("/pay/fail-next")
def pay_fail_next(body: FailNext):
    get_adapter().set_fail_next(body.n)
    return {"ok": True, "fail_next": body.n}


def _dump(s, model) -> list[dict]:
    out = []
    for obj in s.query(model).all():
        row = {}
        for col in model.__table__.columns:
            v = getattr(obj, col.name)
            row[col.name] = iso(v) if isinstance(v, datetime) else v
        out.append(row)
    return out


@router.get("/state")
def state():
    """全表 dump（原始审计视图）：供控制台核对每一步落库结果。"""
    with db.session_scope() as s:
        return {
            **_now(),
            "users": _dump(s, User),
            "merchants": _dump(s, Merchant),
            "commissions": _dump(s, Commission),
            "listings": _dump(s, Listing),
            "orders": _dump(s, Order),
            "feed_cards": _dump(s, FeedCard),
            "pay_logs": _dump(s, PayLog),
            "credit_logs": _dump(s, CreditLog),
            "match_rounds": _dump(s, MatchRound),
            "match_candidates": _dump(s, MatchCandidate),
            "sim_events": _dump(s, SimEvent),
            "idempotency_records": _dump(s, IdempotencyRecord),
            "app_meta": _dump(s, AppMeta),
        }


@router.get("/match-rounds/{listing_id}")
def match_rounds(listing_id: int):
    """公平性快照：每轮 seed + 候选顺位/权重/三系数/越界原因/分配结果（同 seed 可复现）。"""
    with db.session_scope() as s:
        return {"listing_id": listing_id, "rounds": snapshot.round_snapshot(s, listing_id)}


@router.post("/scenario/{name}")
def run_scenario(name: str):
    fn = scenarios.SCENARIOS.get(name)
    if not fn:
        raise NotFound(f"未知场景 {name}（可选 auto/overrun/refund/no_show）")
    return fn()
