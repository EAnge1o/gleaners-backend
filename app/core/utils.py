import random
import uuid
from datetime import datetime, timezone

_sysrandom = random.SystemRandom()


def yuan_to_fen(yuan) -> int:
    return int(round(float(yuan) * 100))


def fen_to_yuan(fen: int) -> float:
    return round(fen / 100.0, 2)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def gen_out_trade_no(order_id: int) -> str:
    return f"GL{order_id:08d}{uuid.uuid4().hex[:8]}"


def gen_pickup_code() -> str:
    return "".join(_sysrandom.choice("0123456789") for _ in range(6))


def gen_seed() -> str:
    return uuid.uuid4().hex[:16]