"""状态机：合法边 / 非法边抛 / suspended 不直达 active / 终态不可逆 / 条件更新裁决。"""
import pytest

from app import db
from app.core.errors import IllegalTransition
from app.core.state_machine import TABLES, can_transition, conditional_transition, set_status
from app.models import Commission, Listing, Order
from tests.conftest import mk_commission, mk_listing, mk_merchant, mk_order, mk_user


def test_legal_edges(client):
    mk_user()
    mk_merchant()
    cid = mk_commission(1, status="draft")
    with db.session_scope() as s:
        c = s.get(Commission, cid)
        for target in ["pending_auth", "active", "paused", "active", "suspended", "pending_auth"]:
            set_status(s, c, target, "commission")
        s.commit()
    lid = mk_listing()
    with db.session_scope() as s:
        l = s.get(Listing, lid)
        for target in ["sold_out", "on_sale", "off_shelf"]:
            set_status(s, l, target, "listing")
        s.commit()
    oid = mk_order(status="pending_confirm", pay_mode="instant")
    with db.session_scope() as s:
        o = s.get(Order, oid)
        for target in ["paying", "paid", "picked_up"]:
            set_status(s, o, target, "order")
        s.commit()


def test_illegal_edges_raise(client):
    mk_user()
    mk_merchant()
    mk_listing(lid=1)
    cid = mk_commission(1)
    with db.session_scope() as s:
        with pytest.raises(IllegalTransition):
            set_status(s, s.get(Commission, cid), "draft", "commission")
    oid = mk_order(status="paid", lid=1)
    with db.session_scope() as s:
        with pytest.raises(IllegalTransition):
            set_status(s, s.get(Order, oid), "paying", "order")
    lid = mk_listing(lid=2, status="off_shelf", qty_available=0)
    with db.session_scope() as s:
        with pytest.raises(IllegalTransition):
            set_status(s, s.get(Listing, lid), "on_sale", "listing")


def test_suspended_no_direct_active(client):
    assert can_transition("commission", "suspended", "active") is False
    mk_user()
    cid = mk_commission(1, status="suspended")
    with db.session_scope() as s:
        with pytest.raises(IllegalTransition):
            set_status(s, s.get(Commission, cid), "active", "commission")


def test_terminal_irreversible(client):
    terminals = {
        "commission": ["completed", "expired", "cancelled"],
        "listing": ["off_shelf"],
        "order": ["picked_up", "no_show", "refunded", "closed"],
    }
    for table, states in terminals.items():
        for st in states:
            assert TABLES[table][st] == set()
            for target in TABLES[table]:
                assert can_transition(table, st, target) is False


def test_conditional_transition_race(client):
    mk_user()
    mk_merchant()
    mk_commission(1)
    mk_listing()
    oid = mk_order(status="paying")
    with db.session_scope() as s:
        assert conditional_transition(s, Order, oid, "paying", "paid") is True
        s.commit()
    with db.session_scope() as s:
        assert conditional_transition(s, Order, oid, "paying", "paid") is False
        s.commit()