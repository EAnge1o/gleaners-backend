# 拾穗 Gleaners 后端 MVP 实现方案（PAYATHON 2026 · Agent Pay）

## Context（为什么做这件事）
用户参加「支付宝 AI 付 PAYATHON 2026 · Agent Pay」赛道，要做成**获奖级别**。产品是一个网页端 AI Agent（手机优先）：用户一句话立规则并授权，Agent 在授权范围内替用户蹲商家余量（临期食品盲盒/全公开）、锁单、付款；越界时暂停让用户确认。上传的《后端 PRD（MVP）》定义了 8 模块、7 核心表、~20 接口、3 个状态机、公平匹配引擎、可切换支付适配层、定时任务与演示控制台。作品提交截止 **12/9**，今天 11/20（约 19 天），需本地全流程彩排、预算严格控制。

工作区为空（全新项目）。已与用户锁定三项基础决策：
- **技术栈**：Python FastAPI + SQLAlchemy 2.x + SQLite(WAL) + APScheduler 3.x + Pydantic v2。
- **范围**：完整后端 8 模块 + 开发期演示控制台 API + 手机优先极简演示前端（全流程可视化）。
- **外部凭证**：仿真优先，零密钥即可本地全流程跑通；LLM 与支付均做适配器，预留真实接口后一键切换。

目标产出：一个可上线架构质感、断网可演示、公平性可审计、每个决策可解释的参赛后端 + 演示前端。

## 三条贯穿性架构决策
1. **虚拟时钟 `Clock`**：所有业务时间判定（有效期/确认超时/取货窗口/定时扫描）一律走 `clock.now()`，禁止直接 `datetime.now()`。offset 持久化到 `app_meta` 表，重启不回退。这是演示控制台「时间快进」成立的前提。
2. **状态机集中执法**：三张转移表集中定义，所有状态变更经唯一入口 `set_status()`，非法转移抛 `IllegalTransition`；关键转移用条件 UPDATE（`WHERE status=旧态`）裁决并发竞态。任何 service 不得直接 `obj.status=...`。
3. **幂等双轨**：HTTP 写接口走 `Idempotency-Key` 中间件（请求级）；支付/授权/退款回调走 `out_trade_no`+条件更新（业务级），两者独立。

金额全程整数「分」，距离全程「米」（haversine），DB 时间存 UTC，前端用 feed 返回的 `server_now`（虚拟时间）校准倒计时。

## 目录结构
```
temp_20261120_04/
├── requirements.txt  .env.example
├── app/
│   ├── main.py            # create_app：路由/中间件/静态/启动钩子(create_all→载时钟→注册job→start→seed)
│   ├── config.py          # Pydantic Settings：PAY_MODE/ENV/LLM_*/SIM_*延迟/SECRET
│   ├── db.py              # engine(WAL+busy_timeout=5000+foreign_keys)、SessionLocal、get_db、Base
│   ├── seed.py            # 幂等种子装载
│   ├── core/              # clock / state_machine / idempotency / errors / security / geo / utils
│   ├── models/            # 全部 ORM 集中(避免循环导入)：user/merchant/commission/listing/order/
│   │                      #   pay_log/credit_log + aux(feed_card/match_round/match_candidate/
│   │                      #   idempotency_record/sim_event/app_meta)
│   ├── modules/
│   │   ├── identity/      # 登录(JWT,种子身份)/用户商家资料；get_current_user/merchant 依赖
│   │   ├── agent/         # llm_adapter(OpenAI兼容)+rule_parser(确定性兜底)+prompts+schemas(RuleCard/ListingDraft)
│   │   ├── commission/    # CRUD/授权/暂停恢复取消/记账/check_completion/quota
│   │   ├── listing/       # 发布/下架/缺货/demand统计 + inventory.py(原子锁/释放)
│   │   ├── matching/      # engine.py / filters.py / lottery.py / snapshot.py
│   │   ├── payment/       # adapter.py / simulated.py / sandbox.py(预留) / factory.py /
│   │   │                  #   service.py / notify.py / sim_events.py / router.py(回调+仿真确认)
│   │   ├── pickup/        # 取货码生成/核销 + credit.py
│   │   ├── feed/          # push_card(同事务) + GET /api/feed?since=(返回 server_now)
│   │   ├── scheduler/     # jobs.py(10个job) + registry.py(register_jobs/run_all_jobs_once)
│   │   └── demo/          # 仅 ENV=dev：seed/快进/fail-next/状态dump/匹配快照/一键场景
│   └── static/            # index.html/app.js/style.css + auth.html/pay.html + vendor/qrcode.min.js(离线)
├── scripts/smoke_test.py  # 端到端彩排(httpx 打真实服务)
└── tests/                 # conftest + test_parser/matching/state_machines/credit/idempotency/
                           #   inventory/payment/refund/weekly_quota
```

## 模块实现要点
- **基础设施 core/**：`Clock.now/advance/reset`；`set_status(db,obj,target,table)`；幂等中间件（键空间=(path,key)，hash 相同回放/异体 409/processing 409，唯一约束兜底并发）；JWT(HS256,role=user|merchant)；Fernet 加密 `pay_account_info`；仿真回调 HMAC-SHA256 验签；`haversine_m`；`out_trade_no`/6 位取货码生成。
- **identity**：`POST /api/auth/login`（种子身份下拉，无密码，查不到按 role 创建）→ JWT。商家端接口强制 `get_current_merchant`，核销/下架/缺货再校验 `merchant_id` 归属。
- **agent**：Pydantic `RuleCard`/`ListingDraft` 即校验闸门（radius 500–3000、金额>0、盲盒 original_price≥price×2.5）。`LLMAdapter` 协议 + `OpenAICompatAdapter`（15s 超时，异常降级）；`rule_parser` 正则/关键词兜底（金额/半径/品类/模式/时段/有效期/指定商家/次数），缺必填或非法→返回追问文案**不入库**。`parse_user_rule`/`parse_merchant_listing`：LLM→兜底→schema 校验→建 draft→push rule_card 卡。
- **commission**：`patch_draft` 仅 draft 可改；`authorize` draft→pending_auth→adapter.authorize→pay_log；`on_auth_notify` 成功→active 存协议号推 auth_result 卡，失败→cancelled；suspended 恢复必须重新 authorize（成功回调 streak 清零）；`check_completion`：single 成交→completed，periodic 月额度耗尽→completed，**周次数用完不改状态**。
- **listing/inventory**：`lock_one` 原子 `UPDATE ... SET qty_available-1,qty_locked+1 WHERE qty_available>0`（rowcount=1 才算锁到）+ 每 listing `threading.Lock` 串行化分配；`release_one` 回补并 sold_out→on_sale；`publish` 同事务提交后触发匹配；`out_of_stock` 对 paid 单逐单退款+关闭未付单释放；`demand` SQL 聚合（3000m 内 active 单按品类×价格档 count）。
- **matching**（技术核心，见下节）。
- **payment**：`PaymentAdapter`(authorize/auto_pay/instant_pay/refund/query/handle_notify)；`factory.get_adapter()` 按 `PAY_MODE=simulate|sandbox|agent_pay` 分发，sandbox 无凭证自动降级 simulate。`SimulatedAdapter`：authorize/auto_pay/refund 写 pay_log(simulated) 并入队 `sim_event`（due=虚拟时间），由 2s job 投递到 notify；instant_pay 返回 `/static/pay.html` + qr_content。`notify.handle(kind,raw)`：验签→落 pay_log 原文→按 out_trade_no 条件更新幂等→首次成功才执行副作用（pay→生成取货码+check_completion+order_success 卡盲盒揭晓；refund→记账回退+refund_notice 卡+重匹配；auth→on_auth_notify）。仿真确认端点 `sim-auth-confirm`/`sim-pay-confirm` 服务端构造签名报文内部调 handle（密钥不出服务端）。`confirm`/`reject` 条件更新裁决与超时 job 竞态。退款回退 `spent_amount_month-=amount`、`used_count_week-=1`（floor 0，仅首次）。
- **pickup/credit**：取货码 6 位 SystemRandom 查重，qr=`GLEANERS:{order_id}:{code}`；`verify` 校验商家归属+时间窗+status=paid→条件更新 picked_up（重复幂等）→credit。`on_picked_up` +1(cap100)/streak=0/win_count_7d+1；`on_no_show` −20(floor0)/streak+1，**streak≥3→该用户全部 active/paused 单 suspended**+推 commission_notice 卡。
- **feed**：`feed_card` 自增 id 即游标，push 与业务同事务；`GET /api/feed?since=&limit=` 返回 `{server_now, cards}`。六类卡：rule_card/auth_result/order_success/overrun_confirm/pickup_reminder/refund_notice（+必要扩展 commission_notice）。
- **scheduler**（全部用 clock.now、条件更新、可重复无副作用）：confirm_timeout(30s)、pay_reconcile(60s,paying>3min→query)、pickup_reminder(60s,距 pickup_start≤30min)、no_show_sweep(5min)、listing_off_shelf(5min)、commission_expire(每天0点)、weekly_reset(周一0点)、monthly_reset(每月1日)、deliver_sim_events(2s)、win_count_recompute(每天0点滑窗)。
- **demo**（仅 dev）：`seed`/`time/fast-forward{seconds}`(=拨钟+立即 run_all_jobs_once)/`time/reset`/`jobs/run`/`pay/fail-next{n}`/`state`/`match-rounds/{listing_id}`(公平性快照)/`scenario/{auto|overrun|refund|no_show}`。

## 匹配引擎核心（matching/）
流程：硬过滤→越界判定→加权抽签→按份分配；每轮留快照（`match_round` seed+phase_cursor+status；`match_candidate` rank+weight+三系数原值+overrun_reasons+allocated_order_id）。
- **硬过滤**（排除）：active 且在有效期/生效时段；mode∈buy_modes；品类规格匹配；指定商家约束；盲盒 rating≥4.3；距离≤3000；public 明确含用户过敏原；周次数已用完；同用户同余量已占份（占份集={pending_confirm,paying,paid,picked_up,no_show}，closed/refunded 不占份）。
- **越界判定**（保留候选不自动付，原因全写 overrun_reasons）：有效上限=min(per_order_max,wallet_single_limit)；OVER_PRICE/OVER_MONTHLY/OUT_OF_RADIUS(radius<dist≤3000)/NEW_MERCHANT(rating_count<10)/ALLERGEN_UNKNOWN(用户有过敏原且余量="未知")/TIME_CONFLICT(取货窗口∩生效时段=∅)。
- **加权不放回抽签**（Efraimidis–Spirakis）：`key=rng.random()**(1/weight)` 降序；weight=信用(credit/100)×轮换(1/(1+win_count_7d))×新客(从未 picked_up=1.5 否则 1)；`rng=random.Random(round.seed)`，**先写 seed 再抽签**保证可复现。排序=lottery(范围内)+lottery(越界)，范围内整体优先。
- **分配循环**（每 listing 锁内，每份一短事务）：取下一候选→revalidate→`lock_one`→范围内建 paying 单+auto_pay（失败释放续配）；越界建 pending_confirm 单锁 3min+push 卡**立即继续下一份不阻塞**；cursor 到底仍有库存→开新轮(新 seed)。
- **续配**：释放触发点四处（确认超时/拒绝、auto_pay 失败、补单关单、下架/缺货退款）调 `continue_allocation`，从持久化 `phase_cursor` 继续、不重抽、逐候选 revalidate。
- **match_reason** JSON：commission_id/品类/price vs 上限/距离 vs 半径/评分/原价/顺位/seed；`humanize_match_reason()`→「面包烘焙·13.9元≤15元·0.8公里·评分4.6」。

## 易踩坑边界清单（编码逐条对照）
1. 锁定释放后从 phase_cursor 续配，不重抽；续配前 revalidate；范围内候选中途变越界则跳过留新轮；轮耗尽仍有库存才开新轮。
2. 退款回退记账 floor 0，仅 refund 首次成功同事务执行；终态委托单不因退款复活。
3. 周次数用完状态仍 active，靠硬过滤跳过，周一 reset 自然恢复；仅月额度耗尽/single 成交才 completed。
4. 盲盒 items 仅 paid 后可见（order_success 卡与 GET order 按状态闸门）；越界卡不泄露 items。
5. 越界创建 pending_confirm 锁 1 份后立即继续循环，不等确认；范围内分完有剩余才进越界段。
6. 占份集含 no_show；closed/refunded 不占份可再参与。
7. 盲盒双门槛（发布+匹配均校验 rating≥4.3、original_price≥price×2.5）；allergens="未知"+用户有过敏原=越界 ALLERGEN_UNKNOWN（非硬排除），public 明确含过敏原才硬排除。
8. 距离三段：≤radius 范围内；radius<dist≤3000 越界；>3000 硬排除；半径 clamp[500,3000]。
9. suspended 无直达 active 边，必须经 pending_auth 重新授权；成功回调 streak 清零。
10. out_trade_no 先写库再调支付；退款用独立 out_request_no 幂等；重复通知返回 success 但不重复生效。
11. 时间冲突是越界（保留发确认卡）不是排除。
12. 虚拟时间一致性：前端倒计时用 server_now 校准；sim_event.due_at/confirm_expires_at/pickup 窗口全虚拟时钟。
13. no_show 不退款不释放库存（份额已消耗），只记信用；此时 listing 已 off_shelf。
14. 取货码核销校验商家归属+时间窗+状态；重复核销幂等返回首次结果。

## 演示前端（app/static/，手机优先单页，原生 JS，无构建链）
hash 路由 + 底部 Tab，桌面居中限宽 480px。视图：①登录（角色 Tab+种子身份下拉）②用户对话流（1.5s 轮询 feed，按 type 渲染 rule_card/auth_result/order_success[取货码+离线二维码+盲盒揭晓+成交理由]/overrun_confirm[原因 chips+server_now 校准倒计时+确认/拒绝]/pickup_reminder/refund_notice；底部一句话输入→parse）③委托单管理（状态徽章+暂停/恢复/取消+额度进度条）④商家主页（一句话发布→草稿→确认发布；demand 统计；今日订单；6 位核销输入）⑤演示控制台抽屉（快进+3min/+35min/+1天/自定义、一键四场景、fail-next、匹配快照公平性展示、重置种子）。auth.html/pay.html 极简独立页调 sim-confirm 端点后回对话流。QR 用离线 vendor 库，无 CDN。

## 种子数据与一键场景
- 用户：小拾（中心 30.2741,120.1551，allergens=["花生"]，wallet=1500 分，credit100）；阿穗（wallet=3000，无过敏原，预置 2 条近 7 天 picked_up→演示降权 vs 新客加成）。
- 商家：麦香烘焙（~400m，4.6/120，sandbox）；绿源生鲜（~2600m，4.5/5→新店越界）；鲜味便当（~900m，4.2→盲盒门槛拦截）。
- 余量模板：面包盲盒 12.9 元/原价 35/3 份/今晚 20:00–22:00；全公开餐食 13.9；越界盲盒 19.9（>15 上限）。
- 四场景（scenarios.py）：auto（自动付）/overrun（OVER_PRICE 或 OUT_OF_RADIUS+NEW_MERCHANT）/refund（缺货退款+回退+重匹配）/no_show（快进过窗口−20，连跑 3 次→suspended）。

## 测试与验证
依赖钉版本：fastapi、uvicorn[standard]、sqlalchemy>=2.0,<2.1、pydantic>=2、pydantic-settings、**apscheduler>=3.10,<4**、httpx、pyjwt、cryptography、python-dotenv、pytest（alipay-sdk-python 可选）。
- 单测：matching（硬过滤逐条/越界六原因/同 seed 同序/范围内优先/三系数）、inventory（20 线程并发 lock 恰好 qty 成功）、state_machines（合法边过/非法边抛/suspended 不直达 active/终态不可逆）、idempotency（同键同体重放/异体 409/回调重复只生效一次/核销幂等）、credit（+1cap100/−20floor0/streak3→suspended/重授权清零）、refund（回退恰好一次 floor0）、weekly_quota（用完仍 active/跳过/reset 恢复）、parser（典型快照/非法追问不入库/盲盒价格比拒绝）、payment（auto 失败续配/补单关单/确认与超时竞态）。
- 端到端 smoke 剧本：seed→login→parse(draft+rule_card)→authorize+sim-auth(active+auth_result)→商家发布 12.9 盲盒(paying→paid+order_success 取货码+match_reason+库存)→核销(picked_up+credit cap+win_count)→overrun(reasons+锁定+其余份数不阻塞→confirm→sim-pay→paid)→拒绝/快进 3min 超时(closed+释放+续配)→缺货(refunded+回退+refund_notice)→快进过 pickup_end(no_show−20+off_shelf)→fail-next(失败释放续配)。

运行命令（PowerShell）：
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
pytest -q
uvicorn app.main:app --host 0.0**.0 --port 8000 --reload   # 手机同局域网 http://<PC_IP>:8000
python scripts\smoke_test.py --base-url http://127.0.0.1:8000   # 另开终端
```

## 分阶段实施（11/20 起）
- **P0 骨架**：requirements/config/db/全部 models/core 四件套/identity 登录/seed。验证：服务起、建表、登录拿 JWT、状态机+幂等单测绿。
- **P1 规则链路**：agent 兜底解析+schema 闸门（LLM 留接口）/commission CRUD+授权仿真/feed 骨架。验证：parse→draft→authorize→active 全通，rule_card/auth_result 卡可见，test_parser 绿。
- **P2 自动付主链路**：listing 发布+原子库存/匹配引擎(过滤+抽签+快照+续配)/仿真 auto_pay/notify 骨架。验证：**auto 场景端到端通**，test_matching/test_inventory 绿，快照可查。（评委第一眼，必须尽早跑通）
- **P3 越界与闭环**：越界确认/拒绝/即时付仿真/全部定时任务/退款回退+重匹配/取货核销+信用。验证：overrun/refund/no_show 三场景通，test_credit/refund/weekly_quota 绿。
- **P4 前端与彩排**：index.html 全视图/卡片组件/auth.html/pay.html/演示控制台+四场景/smoke_test.py。验证：**手机浏览器全流程彩排一次过**，smoke 全绿。
- **P5 打磨备赛**：沙箱 adapter（有余力才接，否则仿真即提交形态）/公平性快照展示页/成交理由文案/录屏路演脚本/全量回归+断网演练。12/9 前留 1 天缓冲。

原则：真实沙箱永远排在仿真全通之后，绝不阻塞主线；提交形态不依赖任何外部凭证。

## 获奖级亮点与风险
亮点（对评委关注点）：**公平性可审计**（seed+三系数+顺位快照，同 seed 可复现，一键展示「为什么是他不是我」）；**可解释性**（match_reason 人类可读+overrun_reasons 全量+raw_text/rule_card 留证）；**合规**（pay_log 全量原文审计、二清红线落地为 pay_channel 隔离、协议授权+额度上限+随时暂停取消、越界必停）；**可演示性**（虚拟时钟+一键场景+smoke 彩排，零等待零外网断网可跑、手机真机体验）；**工程成熟度**（状态机集中执法、双轨幂等、原子防超卖、适配器一键切真实支付宝）。
风险与规避：SQLite 写竞争→WAL+busy_timeout+每 listing 锁+短事务；APScheduler 4.x 漂移→钉<4；LLM 超时/坏 JSON→15s+schema 闸门+兜底默认启用；快进后 job 没跟上→快进=拨钟+立即 run_all_jobs_once+sim_event 虚拟时钟；沙箱凭证拖延→默认 simulate 沙箱仅 P5 可选；确认/超时竞态→条件 UPDATE 裁决；现场断网→全本地+离线 QR；范围蔓延→真实支付/地图/登录全留接口不实现。

## 关键文件
- app/modules/matching/engine.py（匹配主流程，正确性核心）
- app/core/state_machine.py（三状态机唯一执法入口）
- app/modules/payment/simulated.py（仿真支付+sim_event，零密钥全流程基础）
- app/modules/listing/inventory.py（原子防超卖，与续配强耦合）
- app/core/clock.py（虚拟时钟，快进与全部定时判定前提）