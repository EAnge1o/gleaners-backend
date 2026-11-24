"""原子防超卖库存。lock_one 用条件 UPDATE（qty_available>0）裁决，rowcount=1 才算锁到；
每 listing 一把 threading.Lock 串行化分配循环（引擎持有），DB 层原子 UPDATE 兜底并发。
释放回补并 sold_out→on_sale。所有函数不 commit，调用方负责事务边界。
"""
import threading

from sqlalchemy import update

from app.core.state_machine import set_status
from app.models import Listing

_locks: dict[int, threading.Lock] = {}
_guard = threading.Lock()


def listing_lock(listing_id: int) -> threading.Lock:
    with _guard:
        lk = _locks.get(listing_id)
        if lk is None:
            lk = threading.Lock()
            _locks[listing_id] = lk
        return lk


def lock_one(db, listing_id: int) -> bool:
    """原子扣减一份可用库存。成功返回 True（qty_available-1, qty_locked+1）。"""
    res = db.execute(
        update(Listing)
        .where(Listing.id == listing_id, Listing.qty_available > 0)
        .values(
            qty_available=Listing.qty_available - 1,
            qty_locked=Listing.qty_locked + 1,
        )
    )
    db.flush()
    if res.rowcount != 1:
        return False
    l = db.get(Listing, listing_id)
    db.refresh(l)
    if l.qty_available == 0 and l.status == "on_sale":
        set_status(db, l, "sold_out", "listing")
    return True


def release_one(db, listing_id: int):
    """回补一份锁定库存（释放占份），sold_out→on_sale。
    已下架余量不可再售：仅回落锁定数，不回补可用、不复活状态。"""
    l = db.get(Listing, listing_id)
    if not l or l.qty_locked <= 0:
        return
    l.qty_locked -= 1
    if l.status == "off_shelf":
        db.flush()
        return
    l.qty_available += 1
    db.flush()
    if l.status == "sold_out" and l.qty_available > 0:
        set_status(db, l, "on_sale", "listing")


def consume_one(db, listing_id: int):
    """份额已消耗（picked_up/no_show）：锁定数回落，不回补可用库存。"""
    l = db.get(Listing, listing_id)
    if not l or l.qty_locked <= 0:
        return
    l.qty_locked -= 1
    db.flush()


def refresh_status(db, listing_id: int):
    l = db.get(Listing, listing_id)
    if not l or l.status == "off_shelf":
        return
    if l.qty_available == 0 and l.status == "on_sale":
        set_status(db, l, "sold_out", "listing")
    elif l.qty_available > 0 and l.status == "sold_out":
        set_status(db, l, "on_sale", "listing")