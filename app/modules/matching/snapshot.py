"""匹配公平性快照：match_round(seed+phase_cursor+status) + match_candidate(顺位+权重+三系数+越界原因)。
先写 seed 再抽签，同 seed 可复现「为什么是他不是我」。
"""
from app.models import MatchCandidate, MatchRound


def create_round(db, listing_id: int, seed: str) -> MatchRound:
    r = MatchRound(listing_id=listing_id, seed=seed, phase_cursor=0, status="open")
    db.add(r)
    db.flush()
    return r


def write_candidates(db, round_id: int, ranked: list[dict]):
    for rank, it in enumerate(ranked):
        db.add(MatchCandidate(
            round_id=round_id,
            rank=rank,
            commission_id=it["commission_id"],
            user_id=it["user_id"],
            in_range=it["in_range"],
            weight=it["weight"],
            factors=it["factors"],
            overrun_reasons=it["overrun_reasons"],
        ))
    db.flush()


def round_snapshot(db, listing_id: int) -> list[dict]:
    rounds = (
        db.query(MatchRound)
        .filter(MatchRound.listing_id == listing_id)
        .order_by(MatchRound.id)
        .all()
    )
    out = []
    for r in rounds:
        cands = (
            db.query(MatchCandidate)
            .filter(MatchCandidate.round_id == r.id)
            .order_by(MatchCandidate.rank)
            .all()
        )
        out.append({
            "round_id": r.id,
            "seed": r.seed,
            "phase_cursor": r.phase_cursor,
            "status": r.status,
            "candidates": [
                {
                    "rank": c.rank,
                    "commission_id": c.commission_id,
                    "user_id": c.user_id,
                    "in_range": c.in_range,
                    "weight": round(c.weight, 6),
                    "factors": c.factors,
                    "overrun_reasons": c.overrun_reasons,
                    "allocated_order_id": c.allocated_order_id,
                }
                for c in cands
            ],
        })
    return out