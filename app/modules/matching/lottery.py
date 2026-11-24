"""加权不放回抽签（Efraimidis–Spirakis）：key = rng.random()**(1/weight) 降序。

weight = 信用(credit/100) × 轮换(1/(1+win_count_7d)) × 新客(从未 picked_up=1.5 否则 1)。
rng = random.Random(round.seed)，先写 seed 再抽签 → 同 seed 同序，公平性可复现可审计。
"""
import random

WEIGHT_FLOOR = 1e-6


def compute_weight(user, has_picked_up: bool) -> tuple[float, dict]:
    credit_factor = max(user.credit_score, 0) / 100.0
    rotation_factor = 1.0 / (1 + max(user.win_count_7d, 0))
    new_customer_factor = 1.0 if has_picked_up else 1.5
    weight = max(credit_factor * rotation_factor * new_customer_factor, WEIGHT_FLOOR)
    factors = {
        "credit_score": user.credit_score,
        "win_count_7d": user.win_count_7d,
        "is_new_customer": not has_picked_up,
        "credit_factor": round(credit_factor, 4),
        "rotation_factor": round(rotation_factor, 4),
        "new_customer_factor": new_customer_factor,
    }
    return weight, factors


def lottery(items: list[dict], seed: str) -> list[dict]:
    """items 每项含 weight。返回按抽签 key 降序排列的新列表（不放回）。"""
    rng = random.Random(seed)
    keyed = [(rng.random() ** (1.0 / it["weight"]), it) for it in items]
    keyed.sort(key=lambda x: x[0], reverse=True)
    return [it for _, it in keyed]