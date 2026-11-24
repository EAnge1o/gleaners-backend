"""一键演示场景（仅 dev）：auto / overrun / refund / no_show。

复用种子身份 + service 内部调用 + 仿真确认端点，零密钥零等待。
每个场景先重置种子，再沿已验证的端到端路径跑完整流程，返回中文解说步骤与关键 ID，
供演示控制台展示并跳转对话流。所有时间判定走虚拟时钟，快进后立即 run_all_once。
"""
from datetime import timedelta

from app import db
from app.core.clock import clock
from app.models import Commission, Listing, Merchant, Order, User
from app.modules.agent import service as agent_service
from app.modules.commission import service as commission_service
from app.modules.identity.service import user_profile
from app.modules.listing import service as listing_service
from app.modules.order import service as order_service
from app.modules.payment.router import (
    SimAuthConfirm,
    SimPayConfirm,
    sim_auth_confirm,
    sim_pay_confirm,
)
from app.modules.scheduler.jobs import run_all_once
from app.seed import seed

RULE_TEXT = "每单不超过15元，半径1公里"


# ---- 内部助手（每步独立短事务，提交后再触发跨会话副作用）----

def _authorize(uid: int, text: str = RULE_TEXT) -> int:
    with db.session_scope() as s:
        u = s.get(User, uid)
        res = agent_service.parse_user_rule(s, u, text)
        cid = res["commission"]["id"]
        commission_service.authorize(s, u, cid)
    sim_auth_confirm(SimAuthConfirm(commission_id=cid))
    return cid


def _publish(mid: int, price: int = 1390, mode: str = "public", qty: int = 3,
             category: str = "餐食", items=None, allergens=None,
             original_price=None, hours: int = 2) -> dict:
    now = clock.now()
    if items is None:
        items = ["今日余量盲盒"] if mode == "blind" else ["今日便当"]
    if allergens is None:
        allergens = ["未知"] if mode == "blind" else []
    if original_price is None:
        original_price = 5000 if mode == "blind" else 4000
    draft = {
        "mode": mode, "category": category, "items": items, "allergens": allergens,
        "original_price": original_price, "price": price, "qty_total": qty,
        "pickup_start": (now + timedelta(hours=hours)).isoformat(),
        "pickup_end": (now + timedelta(hours=hours + 2)).isoformat(),
    }
    with db.session_scope() as s:
        return listing_service.publish(s, s.get(Merchant, mid), draft)


def _tick(seconds: int = 10):
    clock.advance(seconds)
    run_all_once()


def _orders(uid: int) -> list[dict]:
    with db.session_scope() as s:
        return order_service.list_mine(s, s.get(User, uid))


def _order(oid: int) -> dict:
    with db.session_scope() as s:
        return order_service.to_dict(s, s.get(Order, oid))


def _listing(lid: int) -> dict:
    with db.session_scope() as s:
        return listing_service.to_dict(s, s.get(Listing, lid))


def _commission(cid: int) -> dict:
    with db.session_scope() as s:
        return commission_service.to_dict(s.get(Commission, cid))


def _user(uid: int) -> dict:
    with db.session_scope() as s:
        return user_profile(s.get(User, uid))


def _yuan(fen: int) -> str:
    return f"{fen / 100:.2f}".rstrip("0").rstrip(".")


# ---- 四场景 ----

def scenario_auto() -> dict:
    """自动付：小拾授权 → 麦香 12.9 盲盒 → 范围内自动付款 → 盲盒揭晓 + 取货码。"""
    seed()
    steps = ["重置种子数据", "小拾一句话立规则：每单≤15元·半径1公里"]
    cid = _authorize(1)
    steps.append("协议授权成功，委托单生效（active）")
    l = _publish(1, price=1290, mode="blind", category="面包烘焙",
                 items=["今日面包盲盒"], allergens=["麸质", "牛奶"],
                 original_price=3500, qty=3)
    steps.append("麦香烘焙发布 12.9 元面包盲盒（原价35元·共3份）")
    _tick(10)
    o = _orders(1)[0]
    steps.append(f"公平匹配命中小拾 → 自动付款成功 → 盲盒揭晓：{'、'.join(o['items'] or [])}")
    steps.append(f"取货码 {o['pickup_code']}｜成交理由：{o['match_reason_text']}")
    return {
        "scenario": "auto", "steps": steps, "commission_id": cid,
        "listing_id": l["id"], "order_id": o["id"],
        "order": o, "listing": _listing(l["id"]),
    }


def scenario_overrun() -> dict:
    """越界确认：19.9 盲盒（超价+过敏原未知）与绿源新店（超半径+新店）均锁1份待确认，
    其余份数不阻塞；用户确认麦香单 → 即时付 → 揭晓。绿源单留待用户在对话流交互。"""
    seed()
    steps = ["重置种子数据", "小拾立规则并授权：每单≤15元·半径1公里"]
    cid = _authorize(1)
    l1 = _publish(1, price=1990, mode="blind", category="面包烘焙",
                  items=["超值面包盲盒"], allergens=["未知"], original_price=5000, qty=3)
    steps.append("麦香发布 19.9 元盲盒（>15元上限·过敏原未知）→ 越界锁1份待确认，其余2份不阻塞")
    l2 = _publish(2, price=1290, mode="public", category="生鲜",
                  items=["今日蔬菜包"], allergens=[], original_price=2000, qty=2)
    steps.append("绿源生鲜（新店·约2.6公里）发布 12.9 元蔬菜包 → 越界（超半径+新店）")
    pend = [o for o in _orders(1) if o["status"] == "pending_confirm"]
    o1 = next(o for o in pend if o["listing_id"] == l1["id"])
    o2 = next((o for o in pend if o["listing_id"] == l2["id"]), None)
    steps.append(f"麦香单越界原因：{'、'.join(o1['overrun_reasons'])}（不自动付款，等用户确认）")
    if o2:
        steps.append(f"绿源单越界原因：{'、'.join(o2['overrun_reasons'])}")
    with db.session_scope() as s:
        order_service.confirm(s, s.get(User, 1), o1["id"])
    steps.append("用户确认麦香越界订单 → 生成即时付入口")
    sim_pay_confirm(SimPayConfirm(order_id=o1["id"]))
    paid = _order(o1["id"])
    steps.append(f"付款成功 → 盲盒揭晓：{'、'.join(paid['items'] or [])}｜取货码 {paid['pickup_code']}")
    return {
        "scenario": "overrun", "steps": steps, "commission_id": cid,
        "listing_ids": [l1["id"], l2["id"]], "order_id": o1["id"], "order": paid,
        "pending_order_id": o2["id"] if o2 else None,
        "listings": [_listing(l1["id"]), _listing(l2["id"])],
    }


def scenario_refund() -> dict:
    """缺货退款：自动付 → 商家缺货 → 退款回退记账（floor0）→ 退款不占份 → 补货重匹配。"""
    seed()
    steps = ["重置种子数据", "小拾立规则并授权"]
    cid = _authorize(1)
    l = _publish(1, price=1390, mode="public", qty=1)
    _tick(10)
    o = _orders(1)[0]
    steps.append(f"麦香发布 13.9 元便当（1份）→ 自动付款成功（订单{o['id']}）")
    c = _commission(cid)
    steps.append(f"记账：本周已用{c['used_count_week']}次·本月已花{_yuan(c['spent_amount_month'])}元")
    with db.session_scope() as s:
        listing_service.out_of_stock(s, s.get(Merchant, 1), l["id"])
    steps.append("商家突发缺货 → 触发退款")
    _tick(5)
    ro = _order(o["id"])
    c = _commission(cid)
    steps.append(f"退款 {_yuan(ro['refund_amount'])} 元成功 → 记账回退：本周{c['used_count_week']}次·本月{_yuan(c['spent_amount_month'])}元")
    l2 = _publish(1, price=1390, mode="public", qty=1)
    _tick(10)
    no = next(x for x in _orders(1) if x["listing_id"] == l2["id"])
    steps.append(f"退款不占份 → 麦香补货后小拾再次命中（新订单{no['id']}·{no['status']}）")
    return {
        "scenario": "refund", "steps": steps, "commission_id": cid,
        "refunded_order_id": o["id"], "new_order_id": no["id"],
        "listing_ids": [l["id"], l2["id"]], "commission": c,
    }


def scenario_no_show() -> dict:
    """未取货惩戒：连跑3次「付款→快进过取货窗」→ 每次信用−20，第3次 streak≥3 → 委托单 suspended。"""
    seed()
    steps = ["重置种子数据", "小拾立规则并授权"]
    cid = _authorize(1)
    rounds = []
    for i in range(3):
        l = _publish(1, price=1390, mode="public", qty=1)
        _tick(10)
        o = next(x for x in _orders(1) if x["listing_id"] == l["id"])
        _tick(5 * 3600)  # 快进过 pickup_end（发布后4小时）
        ro = _order(o["id"])
        u = _user(1)
        rounds.append({
            "round": i + 1, "order_id": o["id"], "status": ro["status"],
            "credit_score": u["credit_score"], "no_show_streak": u["no_show_streak"],
        })
        steps.append(f"第{i + 1}次：付款后快进过取货窗 → no_show·信用{u['credit_score']}·连续{u['no_show_streak']}次")
    c = _commission(cid)
    steps.append(f"连续3次未取货 → 委托单 {c['status']}（暂停），须重新授权才能恢复")
    return {
        "scenario": "no_show", "steps": steps, "commission_id": cid,
        "rounds": rounds, "commission_status": c["status"], "user": _user(1),
    }


SCENARIOS = {
    "auto": scenario_auto,
    "overrun": scenario_overrun,
    "refund": scenario_refund,
    "no_show": scenario_no_show,
}
