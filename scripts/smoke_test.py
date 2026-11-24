"""端到端彩排剧本：httpx 打真实服务，零密钥断网可跑。

前置：ENV=dev 启动服务（演示控制台可用）：
    python -m uvicorn app.main:app --port 8000
运行：
    python scripts/smoke_test.py --base-url http://127.0.0.1:8000

覆盖：seed→login→parse→authorize+sim-auth→发布12.9盲盒自动付→核销→越界确认即时付
→拒绝/超时关单释放续配→缺货退款回退→快进过窗no_show→fail-next失败释放续配。
"""
import argparse
import sys
from datetime import datetime, timedelta

import httpx

CHECKS = 0


def ok(cond, msg):
    global CHECKS
    CHECKS += 1
    if not cond:
        print(f"  ✗ FAIL: {msg}")
        raise SystemExit(1)
    print(f"  ✓ {msg}")


def section(title):
    print(f"\n== {title} ==")


class Client:
    def __init__(self, base):
        self.c = httpx.Client(base_url=base, timeout=30)

    def req(self, method, path, h=None, **kw):
        r = self.c.request(method, path, headers=h or {}, **kw)
        if r.status_code >= 400:
            raise SystemExit(f"HTTP {r.status_code} {path}: {r.text}")
        return r.json()

    def login(self, role, key):
        d = self.req("POST", "/api/auth/login", json={"role": role, "identity_key": key})
        return {"Authorization": f"Bearer {d['token']}"}

    def feed(self, h):
        return self.req("GET", "/api/feed?since=0&limit=300", h)

    def now(self, h):
        return datetime.fromisoformat(self.feed(h)["server_now"])

    def orders(self, h):
        return self.req("GET", "/api/orders", h)["items"]

    def order(self, h, oid):
        return self.req("GET", f"/api/orders/{oid}", h)

    def ff(self, seconds):
        return self.req("POST", "/api/demo/time/fast-forward", json={"seconds": seconds})

    def publish(self, mh, now, price=1390, mode="public", qty=3, category="餐食",
                items=None, allergens=None, original_price=None):
        if items is None:
            items = ["今日余量盲盒"] if mode == "blind" else ["今日便当"]
        if allergens is None:
            allergens = ["未知"] if mode == "blind" else []
        if original_price is None:
            original_price = 5000 if mode == "blind" else 4000
        draft = {
            "mode": mode, "category": category, "items": items, "allergens": allergens,
            "original_price": original_price, "price": price, "qty_total": qty,
            "pickup_start": (now + timedelta(hours=2)).isoformat(),
            "pickup_end": (now + timedelta(hours=4)).isoformat(),
        }
        return self.req("POST", "/api/merchant/listings", mh, json=draft)

    def listing(self, mh, lid):
        return self.req("GET", f"/api/merchant/listings/{lid}", mh)

    def authorize(self, h, text="每单不超过15元，半径1公里"):
        d = self.req("POST", "/api/agent/parse", h, json={"text": text})
        ok(d["ok"], "一句话解析成功建草稿")
        cid = d["commission"]["id"]
        a = self.req("POST", f"/api/commissions/{cid}/authorize", h)
        ok(a["auth_url"], "发起授权返回 auth_url")
        self.req("POST", "/api/payment/sim-auth-confirm", json={"commission_id": cid})
        c = self.req("GET", f"/api/commissions/{cid}", h)
        ok(c["status"] == "active", "授权回调后委托单 active")
        return cid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = ap.parse_args()
    cl = Client(args.base_url)

    section("0. 健康检查 + 重置种子")
    health = cl.req("GET", "/api/health")
    ok(health["env"] == "dev", "ENV=dev（演示控制台可用）")
    cl.req("POST", "/api/demo/seed")
    ok(True, "种子重置")

    section("1. 登录 + 一句话立规则 + 授权")
    uh = cl.login("user", "小拾")
    mh = cl.login("merchant", "麦香烘焙")
    cid = cl.authorize(uh)
    types = [c["type"] for c in cl.feed(uh)["cards"]]
    ok("rule_card" in types and "auth_result" in types, "rule_card + auth_result 卡可见")

    section("2. 自动付主链路：12.9 盲盒 → paid → 盲盒揭晓")
    now = cl.now(uh)
    l = cl.publish(mh, now, price=1290, mode="blind", category="面包烘焙",
                   items=["今日面包盲盒"], allergens=["麸质", "牛奶"], original_price=3500, qty=3)
    cl.ff(10)
    o = cl.orders(uh)[0]
    ok(o["status"] == "paid", "范围内自动付款成功 paid")
    ok(o["pickup_code"] and o["items"] == ["今日面包盲盒"], "取货码生成 + 盲盒 paid 后揭晓")
    ok(o["match_reason_text"], f"成交理由可读：{o['match_reason_text']}")
    ld = cl.listing(mh, l["id"])
    ok(ld["qty_available"] == 2 and ld["qty_locked"] == 1, "库存原子锁定：可用2·锁定1")
    ok(any(c["type"] == "order_success" for c in cl.feed(uh)["cards"]), "order_success 卡推送")

    section("3. 取货核销 + 信用")
    cl.ff(2 * 3600 + 60)  # 进入取货窗
    v = cl.req("POST", "/api/merchant/verify-code", mh, json={"code": o["pickup_code"]})
    ok(v["order"]["status"] == "picked_up", "核销成功 picked_up")
    me = cl.req("GET", "/api/me", uh)
    ok(me["credit_score"] == 100 and me["win_count_7d"] == 1, "信用 cap100 + 近7天成交+1")

    section("4. 越界确认：19.9 盲盒 → 锁1份不阻塞 → 确认即时付")
    now = cl.now(uh)
    l2 = cl.publish(mh, now, price=1990, mode="blind", category="面包烘焙",
                    items=["超值面包盲盒"], allergens=["未知"], original_price=5000, qty=3)
    ov = next(x for x in cl.orders(uh) if x["listing_id"] == l2["id"])
    ok(ov["status"] == "pending_confirm", "越界建 pending_confirm")
    ok("OVER_PRICE" in ov["overrun_reasons"] and "ALLERGEN_UNKNOWN" in ov["overrun_reasons"],
       "越界原因 OVER_PRICE + ALLERGEN_UNKNOWN")
    ok(ov["items"] is None, "越界卡不泄露盲盒 items")
    ld2 = cl.listing(mh, l2["id"])
    ok(ld2["qty_available"] == 2 and ld2["qty_locked"] == 1, "越界锁1份·其余2份不阻塞")
    cf = cl.req("POST", f"/api/orders/{ov['id']}/confirm", uh)
    ok(cf["pay_url"], "确认返回即时付入口")
    cl.req("POST", "/api/payment/sim-pay-confirm", json={"order_id": ov["id"]})
    paid = cl.order(uh, ov["id"])
    ok(paid["status"] == "paid" and paid["items"] == ["超值面包盲盒"], "即时付成功 + 揭晓")

    section("5. 拒绝 / 超时关单 → 释放 → 续配")
    now = cl.now(uh)
    l3 = cl.publish(mh, now, price=1990, mode="blind", category="面包烘焙",
                    items=["临期面包盲盒"], allergens=["未知"], original_price=5000, qty=1)
    a = next(x for x in cl.orders(uh) if x["listing_id"] == l3["id"])
    cl.req("POST", f"/api/orders/{a['id']}/reject", uh)
    ok(cl.order(uh, a["id"])["status"] == "closed", "拒绝 → closed")
    b = next(x for x in cl.orders(uh) if x["listing_id"] == l3["id"] and x["status"] == "pending_confirm")
    ok(b["id"] != a["id"], "释放后续配重新命中（新待确认单）")
    cl.ff(200)  # >3min 确认超时
    ok(cl.order(uh, b["id"])["status"] == "closed", "快进超时 → 关单释放")

    section("6. 缺货退款 → 记账回退 → 重匹配")
    now = cl.now(uh)
    l4 = cl.publish(mh, now, price=1390, mode="public", qty=1)
    cl.ff(10)
    d = next(x for x in cl.orders(uh) if x["listing_id"] == l4["id"])
    ok(d["status"] == "paid", "自动付成功")
    c_before = cl.req("GET", f"/api/commissions/{cid}", uh)
    ok(c_before["used_count_week"] >= 1 and c_before["spent_amount_month"] >= 1390, "成交记账")
    cl.req("POST", f"/api/merchant/listings/{l4['id']}/out_of_stock", mh)
    cl.ff(5)
    rd = cl.order(uh, d["id"])
    ok(rd["status"] == "refunded" and rd["refund_amount"] == 1390, "缺货退款 refunded")
    c_after = cl.req("GET", f"/api/commissions/{cid}", uh)
    ok(c_after["spent_amount_month"] == c_before["spent_amount_month"] - 1390
       and c_after["used_count_week"] == c_before["used_count_week"] - 1, "记账回退恰好一次")
    ok(any(x["type"] == "refund_notice" for x in cl.feed(uh)["cards"]), "refund_notice 卡推送")
    ok(cl.listing(mh, l4["id"])["status"] == "off_shelf", "缺货余量下架")

    section("7. 快进过取货窗 → no_show 信用惩戒")
    now = cl.now(uh)
    l5 = cl.publish(mh, now, price=1390, mode="public", qty=1)
    cl.ff(10)
    e = next(x for x in cl.orders(uh) if x["listing_id"] == l5["id"])
    ok(e["status"] == "paid", "自动付成功")
    # 记录快进前所有 paid 单（含此前越界已付单），过窗后按实际新增 no_show 数计扣减
    paid_before = [x["id"] for x in cl.orders(uh) if x["status"] == "paid"]
    credit_before = cl.req("GET", "/api/me", uh)["credit_score"]
    cl.ff(5 * 3600)
    ok(cl.order(uh, e["id"])["status"] == "no_show", "过窗未取 → no_show")
    strikes = sum(1 for oid in paid_before if cl.order(uh, oid)["status"] == "no_show")
    ok(cl.req("GET", "/api/me", uh)["credit_score"] == max(0, credit_before - 20 * strikes),
       f"信用 −20×{strikes}（每笔过窗未取各扣20）")
    ok(cl.listing(mh, l5["id"])["status"] == "off_shelf", "no_show 时余量已下架")

    section("8. fail-next：支付失败 → 释放 → 续配下一候选")
    uh2 = cl.login("user", "阿穗")
    cl.authorize(uh2)
    cl.req("POST", "/api/demo/pay/fail-next", json={"n": 1})
    now = cl.now(uh)
    l6 = cl.publish(mh, now, price=1390, mode="public", qty=2)
    cl.ff(10)
    os6 = [x for x in cl.orders(uh) if x["listing_id"] == l6["id"]]
    os6 += [x for x in cl.orders(uh2) if x["listing_id"] == l6["id"]]
    ok(any(x["status"] == "closed" for x in os6), "首次 auto_pay 失败 → 关单释放")
    ok(any(x["status"] == "paid" for x in os6), "续配下一候选 → 付款成功")
    ld6 = cl.listing(mh, l6["id"])
    occupying = [x for x in os6 if x["status"] in
                 ("pending_confirm", "paying", "paid", "picked_up", "no_show")]
    ok(ld6["qty_locked"] == len(occupying), "锁定数 = 占份订单数（失败单已释放不占份）")
    ok(ld6["qty_available"] == 2 - ld6["qty_locked"], "库存守恒：可用 = 总数 − 锁定")

    print(f"\nPASS ✅  全部 {CHECKS} 项断言通过（端到端彩排一次过）")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
