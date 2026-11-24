/* 拾穗 Gleaners · 手机优先单页（原生 JS，无构建链，离线 QR） */
(function () {
  "use strict";

  // ---------- 基础工具 ----------
  const $ = (s, r) => (r || document).querySelector(s);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const yuan = (fen) => (fen == null ? "—" : (fen / 100).toFixed(fen % 100 === 0 ? 0 : 2));
  const pad = (n) => String(n).padStart(2, "0");
  function wallTime(ms) { const d = new Date(ms); return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`; }
  function wallDT(iso) { if (!iso) return "—"; const d = new Date(iso); return `${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`; }

  let serverOffset = 0;
  const virtualNow = () => Date.now() + serverOffset;
  function calibrate(iso) { if (iso) serverOffset = new Date(iso).getTime() - Date.now(); }

  const SKEY = "gleaners_session";
  const getSession = () => { try { return JSON.parse(localStorage.getItem(SKEY)); } catch { return null; } };
  const setSession = (s) => localStorage.setItem(SKEY, JSON.stringify(s));
  const clearSession = () => localStorage.removeItem(SKEY);

  async function api(path, opts) {
    opts = opts || {};
    const s = getSession();
    const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    if (s && s.token) headers["Authorization"] = "Bearer " + s.token;
    const res = await fetch(path, {
      method: opts.method || "GET", headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
    let data = null; try { data = await res.json(); } catch (e) { /* ignore */ }
    if (res.status === 401) { clearSession(); navigate("#login"); throw new Error("登录已过期，请重新登录"); }
    if (!res.ok) throw new Error((data && (data.error || data.detail)) || ("请求失败 " + res.status));
    return data;
  }

  let toastTimer = null;
  function toast(msg) {
    const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), 2600);
  }

  function renderQR(canvas, text) {
    if (!window.qrcode || !text) return;
    try {
      const qr = qrcode(0, "M"); qr.addData(text); qr.make();
      const n = qr.getModuleCount(), quiet = 4;
      const scale = Math.max(2, Math.floor(168 / (n + quiet * 2)));
      const size = (n + quiet * 2) * scale;
      canvas.width = size; canvas.height = size;
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, size, size);
      ctx.fillStyle = "#2c2820";
      for (let r = 0; r < n; r++) for (let c = 0; c < n; c++)
        if (qr.isDark(r, c)) ctx.fillRect((c + quiet) * scale, (r + quiet) * scale, scale, scale);
    } catch (e) { /* 离线 QR 失败时仅展示取货码 */ }
  }
  const paintQRs = (root) => root.querySelectorAll("canvas[data-qr]").forEach((cv) => renderQR(cv, cv.getAttribute("data-qr")));

  function bind(root, selector, handler) {
    root.addEventListener("click", (e) => {
      const t = e.target.closest(selector);
      if (t && root.contains(t)) handler(t, e);
    });
  }

  // ---------- 文案映射 ----------
  const REASON_CN = {
    OVER_PRICE: "超出每单上限", OVER_MONTHLY: "超出本月额度", OUT_OF_RADIUS: "超出设定半径",
    NEW_MERCHANT: "新店（评价数<10）", ALLERGEN_UNKNOWN: "过敏原未知", TIME_CONFLICT: "取货时段冲突",
  };
  const C_STATUS = {
    draft: ["草稿", "b-gray"], pending_auth: ["待授权", "b-warn"], active: ["生效中", "b-green"],
    paused: ["已暂停", "b-warn"], suspended: ["已冻结", "b-danger"], completed: ["已完成", "b-blue"],
    cancelled: ["已取消", "b-gray"], expired: ["已过期", "b-gray"],
  };
  const O_STATUS = {
    pending_confirm: ["待确认", "b-warn"], paying: ["支付中", "b-blue"], paid: ["已支付", "b-green"],
    picked_up: ["已取货", "b-green"], no_show: ["未取货", "b-danger"], refunded: ["已退款", "b-gray"], closed: ["已关闭", "b-gray"],
  };
  const L_STATUS = { on_sale: ["在售", "b-green"], sold_out: ["售罄", "b-warn"], off_shelf: ["已下架", "b-gray"] };
  const badge = (st, map) => { const m = (map || {})[st] || [st, "b-gray"]; return `<span class="badge ${m[1]}">${esc(m[0])}</span>`; };
  const modeCN = (m) => (m === "blind" ? "盲盒" : "全公开");

  function ruleSummary(rc) {
    if (!rc) return "";
    const chips = [];
    if (rc.categories) chips.push(esc(rc.categories.join("·")));
    if (rc.per_order_max != null) chips.push(`每单≤${yuan(rc.per_order_max)}元`);
    if (rc.radius_m != null) chips.push(`半径${(rc.radius_m / 1000).toFixed(1)}公里`);
    if (rc.buy_modes) chips.push(rc.buy_modes.map(modeCN).join("+"));
    if (rc.weekly_count_max != null) chips.push(`每周${rc.weekly_count_max}次`);
    if (rc.monthly_amount_max != null) chips.push(`每月≤${yuan(rc.monthly_amount_max)}元`);
    if (rc.valid_days != null) chips.push(`有效${rc.valid_days}天`);
    if (rc.active_time_start) chips.push(`时段${rc.active_time_start}-${rc.active_time_end}`);
    return `<div class="chips">${chips.map((c) => `<span class="chip">${c}</span>`).join("")}</div>`;
  }

  // ---------- 路由 ----------
  let chatTimer = null;
  const chat = { cursor: 0, items: [], resolved: new Set(), orderStatus: {}, commissionStatus: {} };
  window.__env = "prod";

  function navigate(hash) { if (location.hash !== hash) location.hash = hash; else render(); }
  function currentRoute() { return location.hash || "#login"; }

  function render() {
    clearInterval(chatTimer); chatTimer = null;
    const s = getSession();
    let route = currentRoute();
    if (!s && route !== "#login") route = "#login";
    if (s && route === "#login") route = s.role === "merchant" ? "#merchant" : "#chat";
    renderTopbar(s, route);
    renderTabbar(s, route);
    const view = $("#view");
    if (route === "#login") viewLogin(view);
    else if (route === "#chat") viewChat(view);
    else if (route === "#commissions") viewCommissions(view);
    else if (route === "#merchant") viewMerchant(view);
    else viewChat(view);
  }

  function renderTopbar(s, route) {
    const bar = $("#topbar");
    if (!s || route === "#login") {
      bar.innerHTML = `<div class="brand">拾穗<small>GLEANERS · AI 余量委托</small></div>`;
      return;
    }
    const name = s.role === "merchant" ? s.profile.name : s.profile.nickname;
    const demoBtn = window.__env === "dev" ? `<button class="top-btn demo" id="btn-demo">控制台</button>` : "";
    bar.innerHTML = `
      <div class="brand">拾穗<small>GLEANERS</small></div>
      <span class="clock-chip" data-clock title="虚拟时钟">--:--:--</span>
      ${demoBtn}
      <button class="top-btn" id="btn-who">${esc(name)}</button>
      <button class="top-btn" id="btn-logout">退出</button>`;
    $("#btn-logout").onclick = () => { clearSession(); navigate("#login"); };
    $("#btn-who").onclick = () => toast(s.role === "merchant" ? "商家身份" : `信用${s.profile.credit_score}·近7天成交${s.profile.win_count_7d}`);
    if ($("#btn-demo")) $("#btn-demo").onclick = openDrawer;
  }

  function renderTabbar(s, route) {
    const bar = $("#tabbar");
    if (!s || route === "#login") { bar.innerHTML = ""; return; }
    const tabs = s.role === "merchant"
      ? [["#merchant", "🏪", "商家主页"]]
      : [["#chat", "💬", "对话"], ["#commissions", "📋", "委托单"]];
    bar.innerHTML = tabs.map(([h, ico, label]) =>
      `<button class="tab ${route === h ? "active" : ""}" data-h="${h}"><span class="ico">${ico}</span>${label}</button>`).join("");
    bind(bar, ".tab", (t) => navigate(t.getAttribute("data-h")));
  }

  // ---------- 登录 ----------
  async function viewLogin(view) {
    view.innerHTML = `
      <div class="page"><div class="login-wrap">
        <div class="login-hero"><div class="logo">🌾</div><h1>拾穗 Gleaners</h1>
          <p>一句话立规则 · AI 替你蹲余量 · 越界必停</p></div>
        <div class="role-tabs">
          <button data-role="user" class="active">我是用户</button>
          <button data-role="merchant">我是商家</button>
        </div>
        <label class="field">选择种子身份（免密登录）</label>
        <select id="identity"></select>
        <div class="btn-row"><button class="btn block green" id="btn-login">进入</button></div>
        <p class="muted" style="text-align:center;margin-top:18px">仿真支付 · 零密钥 · 断网可演示</p>
      </div></div>`;
    let role = "user";
    const idSel = $("#identity");
    async function loadIdentities() {
      try {
        const d = await api("/api/auth/identities");
        const list = role === "user" ? d.users.map((u) => [u.nickname, u.nickname]) : d.merchants.map((m) => [m.name, m.name]);
        idSel.innerHTML = list.map(([v, t]) => `<option value="${esc(v)}">${esc(t)}</option>`).join("");
      } catch (e) { idSel.innerHTML = `<option value="${role === "user" ? "小拾" : "麦香烘焙"}">${role === "user" ? "小拾" : "麦香烘焙"}</option>`; }
    }
    bind(view, ".role-tabs button", (t) => {
      role = t.getAttribute("data-role");
      view.querySelectorAll(".role-tabs button").forEach((b) => b.classList.toggle("active", b === t));
      loadIdentities();
    });
    $("#btn-login").onclick = async () => {
      try {
        const d = await api("/api/auth/login", { method: "POST", body: { role, identity_key: idSel.value } });
        setSession({ token: d.token, role: d.role, profile: d.profile });
        navigate(d.role === "merchant" ? "#merchant" : "#chat");
      } catch (e) { toast(e.message); }
    };
    loadIdentities();
  }

  // ---------- 用户对话流 ----------
  function viewChat(view) {
    view.innerHTML = `
      <div class="chat">
        <div class="chat-msgs" id="chat-msgs"></div>
        <div class="chat-input">
          <input id="rule-input" placeholder="一句话立规则，如：每单不超过15元，半径1公里，面包烘焙" autocomplete="off">
          <button class="btn" id="btn-send">发送</button>
        </div>
      </div>`;
    const msgs = $("#chat-msgs");
    bind(msgs, "[data-action]", (t) => {
      const act = t.getAttribute("data-action");
      if (act === "authorize") doAuthorize(t.getAttribute("data-cid"));
      else if (act === "confirm") doConfirm(t.getAttribute("data-oid"));
      else if (act === "reject") doReject(t.getAttribute("data-oid"));
    });
    const send = () => sendRule();
    $("#btn-send").onclick = send;
    $("#rule-input").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
    mountChat();
  }

  async function mountChat() {
    chat.cursor = 0; chat.items = []; chat.resolved = new Set(); chat.orderStatus = {}; chat.commissionStatus = {};
    try {
      const d = await api("/api/feed?since=0&limit=300");
      calibrate(d.server_now);
      d.cards.forEach((c) => { chat.items.push({ kind: "card", card: c }); chat.cursor = Math.max(chat.cursor, c.id); });
    } catch (e) { /* ignore */ }
    await refreshOrderStatus();
    renderChatMsgs(); scrollBottom();
    clearInterval(chatTimer); chatTimer = setInterval(pollFeed, 1500);
  }

  async function refreshOrderStatus() {
    try {
      const [od, cd] = await Promise.all([api("/api/orders"), api("/api/commissions")]);
      (od.items || []).forEach((o) => { chat.orderStatus[o.id] = o.status; });
      (cd.items || []).forEach((c) => { chat.commissionStatus[c.id] = c.status; });
    } catch (e) { /* ignore */ }
  }

  async function pollFeed() {
    try {
      const d = await api(`/api/feed?since=${chat.cursor}&limit=100`);
      calibrate(d.server_now);
      if (d.cards && d.cards.length) {
        d.cards.forEach((c) => { chat.items.push({ kind: "card", card: c }); chat.cursor = Math.max(chat.cursor, c.id); });
        await refreshOrderStatus();
        renderChatMsgs(); scrollBottom();
      }
    } catch (e) { /* 静默重试 */ }
  }

  function scrollBottom() { const m = $("#chat-msgs"); if (m) m.scrollTop = m.scrollHeight; }

  function renderChatMsgs() {
    const m = $("#chat-msgs"); if (!m) return;
    if (!chat.items.length) {
      m.innerHTML = `<div class="empty"><span class="ico">🌾</span>还没有消息。<br>在下方输入一句话规则，AI 替你蹲余量。</div>`;
      return;
    }
    m.innerHTML = chat.items.map((it) => {
      if (it.kind === "user") return `<div class="bubble-user">${esc(it.text)}</div>`;
      if (it.kind === "agent") return `<div class="bubble-agent">🤖 ${esc(it.text)}</div>`;
      return cardHTML(it.card);
    }).join("");
    paintQRs(m);
  }

  async function sendRule() {
    const inp = $("#rule-input"); const text = inp.value.trim(); if (!text) return;
    inp.value = "";
    chat.items.push({ kind: "user", text }); renderChatMsgs(); scrollBottom();
    try {
      const d = await api("/api/agent/parse", { method: "POST", body: { text } });
      if (!d.ok) {
        chat.items.push({ kind: "agent", text: (d.questions || ["请补充规则"]).join(" ") });
        renderChatMsgs(); scrollBottom();
      }
    } catch (e) { toast(e.message); }
  }

  async function doAuthorize(cid) {
    try {
      const d = await api(`/api/commissions/${cid}/authorize`, { method: "POST" });
      chat.resolved.add("c" + cid);
      if (d.auth_url) location.href = d.auth_url; else renderChatMsgs();
    } catch (e) { toast(e.message); renderChatMsgs(); }
  }
  async function doConfirm(oid) {
    try {
      const d = await api(`/api/orders/${oid}/confirm`, { method: "POST" });
      chat.resolved.add(oid);
      if (d.pay_url) location.href = d.pay_url; else renderChatMsgs();
    } catch (e) { toast(e.message); renderChatMsgs(); }
  }
  async function doReject(oid) {
    try {
      await api(`/api/orders/${oid}/reject`, { method: "POST" });
      chat.resolved.add(oid); renderChatMsgs(); toast("已拒绝，份额释放并续配");
    } catch (e) { toast(e.message); renderChatMsgs(); }
  }

  // ---------- 卡片渲染 ----------
  function fcHead(title, color, time) {
    return `<div class="fc-head"><span class="tag ${color}">${title}</span><span class="fc-time">${wallDT(time)}</span></div>`;
  }
  function cardHTML(c) {
    const p = c.payload || {};
    switch (c.type) {
      case "rule_card": {
        const cs = chat.commissionStatus[p.commission_id];
        const canAuth = p.commission_id && p.status === "draft" && !chat.resolved.has("c" + p.commission_id) && (!cs || cs === "draft");
        const tail = canAuth
          ? `<div class="btn-row"><button class="btn green sm" data-action="authorize" data-cid="${p.commission_id}">授权并生效</button></div>`
          : (cs && cs !== "draft" ? `<div class="btn-row">${badge(cs, C_STATUS)}</div>` : "");
        return `<div class="feed-card">${fcHead("规则卡片", "b-brand", c.created_at)}<div class="fc-body">
          <div class="muted" style="margin-bottom:4px">「${esc(p.raw_text)}」</div>
          ${ruleSummary(p.rule_card)}
          ${tail}
        </div></div>`;
      }
      case "auth_result": {
        const okc = p.success;
        return `<div class="feed-card">${fcHead("授权结果", okc ? "b-green" : "b-danger", c.created_at)}<div class="fc-body">
          <div>${okc ? "✅ 协议授权成功，委托单已生效" : "❌ 授权失败，委托单已取消"}</div>
          ${p.agreement_no ? `<div class="muted">协议号 ${esc(p.agreement_no)}</div>` : ""}
        </div></div>`;
      }
      case "order_success": {
        return `<div class="feed-card">${fcHead("成交成功", "b-green", c.created_at)}<div class="fc-body">
          <div class="kv"><span class="k">${esc(p.merchant_name)} · ${esc(p.category)}</span><span class="price">¥${yuan(p.amount)}</span></div>
          <div class="reveal">🎁 盲盒揭晓：${esc((p.items || []).join("、"))}</div>
          <div class="pickup-code">${esc(p.pickup_code)}</div>
          <div class="qr-box"><canvas data-qr="${esc(p.qr_content)}"></canvas></div>
          <div class="muted" style="text-align:center">取货窗 ${wallDT(p.pickup_start)} ~ ${wallDT(p.pickup_end)}</div>
          <div class="reveal" style="background:var(--blue-soft);color:#2f5578">📐 ${esc(p.match_reason_text)}</div>
        </div></div>`;
      }
      case "overrun_confirm": {
        const st = chat.orderStatus[p.order_id];
        const resolved = chat.resolved.has(String(p.order_id)) || chat.resolved.has(p.order_id) || (st && st !== "pending_confirm");
        const chips = (p.overrun_reasons || []).map((r) => `<span class="chip danger">${esc(REASON_CN[r] || r)}</span>`).join("");
        const btns = resolved ? `<div class="muted">已处理 ${st ? badge(st, O_STATUS) : ""}</div>` :
          `<div class="btn-row"><button class="btn green sm" data-action="confirm" data-oid="${p.order_id}">确认并支付</button>
           <button class="btn danger sm" data-action="reject" data-oid="${p.order_id}">拒绝</button></div>`;
        return `<div class="feed-card">${fcHead("越界待确认", "b-warn", c.created_at)}<div class="fc-body">
          <div class="kv"><span class="k">${esc(p.merchant_name)} · ${esc(p.category)}</span><span class="price">¥${yuan(p.price)}</span></div>
          <div class="muted">距离 ${(p.distance_m / 1000).toFixed(1)} 公里 · 超出授权范围，已暂停自动付款</div>
          <div class="chips">${chips}</div>
          <div class="muted">确认倒计时 <span class="countdown" data-expires="${esc(p.confirm_expires_at)}">--:--</span></div>
          ${btns}
        </div></div>`;
      }
      case "pickup_reminder": {
        return `<div class="feed-card">${fcHead("取货提醒", "b-blue", c.created_at)}<div class="fc-body">
          <div>⏰ ${esc(p.merchant_name)} · ${esc(p.category)} 即将开始取货</div>
          <div class="pickup-code" style="font-size:24px">${esc(p.pickup_code)}</div>
          <div class="qr-box"><canvas data-qr="${esc(p.qr_content)}"></canvas></div>
          <div class="muted" style="text-align:center">取货窗 ${wallDT(p.pickup_start)} ~ ${wallDT(p.pickup_end)}</div>
        </div></div>`;
      }
      case "refund_notice": {
        return `<div class="feed-card">${fcHead("退款通知", "b-gray", c.created_at)}<div class="fc-body">
          <div>💸 ${esc(p.merchant_name)} 退款 <span class="price">¥${yuan(p.refund_amount)}</span></div>
          <div class="muted">原因：${esc(p.reason || "商家缺货")} · 额度已回退，可继续蹲新余量</div>
        </div></div>`;
      }
      case "commission_notice": {
        const danger = p.status === "suspended";
        return `<div class="feed-card">${fcHead("委托单通知", danger ? "b-danger" : "b-blue", c.created_at)}<div class="fc-body">
          <div>${danger ? "⚠️" : "ℹ️"} 委托单 #${p.commission_id} ${esc((C_STATUS[p.status] || [p.status])[0])}</div>
          <div class="muted">${esc(p.reason || "")}</div>
        </div></div>`;
      }
      default:
        return `<div class="feed-card">${fcHead(c.type, "b-gray", c.created_at)}<div class="fc-body"><pre class="dump">${esc(JSON.stringify(p, null, 2))}</pre></div></div>`;
    }
  }

  // ---------- 委托单管理 ----------
  async function viewCommissions(view) {
    view.innerHTML = `<div class="page" id="c-page"><div class="empty">加载中…</div></div>`;
    const page = $("#c-page");
    bind(page, "[data-action]", (t) => {
      const act = t.getAttribute("data-action"), cid = t.getAttribute("data-cid");
      if (act === "authorize") commissionAuthorize(cid);
      else if (act === "goauth") location.href = `/auth.html?commission_id=${cid}`;
      else if (act === "resume") commissionResume(cid);
      else if (act === "pause") commissionAction(cid, "pause", "已暂停");
      else if (act === "cancel") commissionAction(cid, "cancel", "已取消");
    });
    await loadCommissions();
  }
  async function loadCommissions() {
    const page = $("#c-page"); if (!page) return;
    try {
      const [cs, quota] = await Promise.all([api("/api/commissions"), api("/api/me/quota")]);
      const qmap = {}; (quota.items || []).forEach((q) => { qmap[q.commission_id] = q; });
      if (!cs.items.length) { page.innerHTML = `<div class="empty"><span class="ico">📋</span>还没有委托单。<br>去对话流一句话立规则。</div>`; return; }
      page.innerHTML = `<div class="section-title">我的委托单</div>` + cs.items.map((c) => commissionCard(c, qmap[c.id])).join("");
    } catch (e) { page.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  }
  function quotaBar(label, used, max, isMoney) {
    if (max == null) return `<div class="kv"><span class="k">${label}</span><span>${isMoney ? "¥" + yuan(used) : used} · 不限</span></div>`;
    const pct = Math.min(100, Math.round((used / max) * 100));
    const warn = pct >= 80 ? "warn" : "";
    const txt = isMoney ? `¥${yuan(used)} / ¥${yuan(max)}` : `${used} / ${max}`;
    return `<div class="kv"><span class="k">${label}</span><span>${txt}</span></div><div class="progress ${warn}"><i style="width:${pct}%"></i></div>`;
  }
  function commissionCard(c, q) {
    let actions = "";
    if (c.status === "draft") actions = `<button class="btn green sm" data-action="authorize" data-cid="${c.id}">授权生效</button><button class="btn danger sm" data-action="cancel" data-cid="${c.id}">取消</button>`;
    else if (c.status === "pending_auth") actions = `<button class="btn green sm" data-action="goauth" data-cid="${c.id}">去授权</button><button class="btn danger sm" data-action="cancel" data-cid="${c.id}">取消</button>`;
    else if (c.status === "active") actions = `<button class="btn ghost sm" data-action="pause" data-cid="${c.id}">暂停</button><button class="btn danger sm" data-action="cancel" data-cid="${c.id}">取消</button>`;
    else if (c.status === "paused") actions = `<button class="btn green sm" data-action="resume" data-cid="${c.id}">恢复</button><button class="btn danger sm" data-action="cancel" data-cid="${c.id}">取消</button>`;
    else if (c.status === "suspended") actions = `<button class="btn green sm" data-action="resume" data-cid="${c.id}">重新授权</button><button class="btn danger sm" data-action="cancel" data-cid="${c.id}">取消</button>`;
    const quotaHTML = q ? quotaBar("本周次数", q.weekly.used, q.weekly.max, false) + quotaBar("本月额度", q.monthly.used, q.monthly.max, true) : "";
    return `<div class="card">
      <div class="kv"><span style="font-weight:700">#${c.id} ${esc(c.raw_text || "")}</span>${badge(c.status, C_STATUS)}</div>
      ${ruleSummary(c.rule_card)}
      ${quotaHTML}
      <div class="muted">有效期 ${wallDT(c.valid_from)} ~ ${wallDT(c.valid_to)}</div>
      ${actions ? `<div class="btn-row">${actions}</div>` : ""}
    </div>`;
  }
  async function commissionAuthorize(cid) {
    try { const d = await api(`/api/commissions/${cid}/authorize`, { method: "POST" }); if (d.auth_url) location.href = d.auth_url; else loadCommissions(); }
    catch (e) { toast(e.message); }
  }
  async function commissionResume(cid) {
    try { const d = await api(`/api/commissions/${cid}/resume`, { method: "POST" }); if (d.auth_url) location.href = d.auth_url; else { toast("已恢复"); loadCommissions(); } }
    catch (e) { toast(e.message); }
  }
  async function commissionAction(cid, act, msg) {
    try { await api(`/api/commissions/${cid}/${act}`, { method: "POST" }); toast(msg); loadCommissions(); }
    catch (e) { toast(e.message); }
  }

  // ---------- 商家主页 ----------
  let merchantDraft = null;
  async function viewMerchant(view) {
    view.innerHTML = `<div class="page" id="m-page"><div class="empty">加载中…</div></div>`;
    const page = $("#m-page");
    bind(page, "[data-action]", (t) => {
      const act = t.getAttribute("data-action"), lid = t.getAttribute("data-lid");
      if (act === "parse") merchantParse();
      else if (act === "publish-draft") merchantPublish();
      else if (act === "off_shelf") merchantListingAction(lid, "off_shelf", "已下架");
      else if (act === "out_of_stock") merchantListingAction(lid, "out_of_stock", "已标记缺货并退款");
      else if (act === "snapshot") { openDrawer(); loadSnapshot(lid); }
      else if (act === "verify") merchantVerify();
    });
    await mountMerchant();
  }
  async function mountMerchant() {
    const page = $("#m-page"); if (!page) return;
    const s = getSession();
    try {
      const [ls, demand, orders] = await Promise.all([
        api("/api/merchant/listings"), api("/api/merchant/demand"), api("/api/merchant/orders"),
      ]);
      page.innerHTML = `
        <div class="card">
          <div class="kv"><span style="font-weight:800;font-size:16px">🏪 ${esc(s.profile.name)}</span><span class="badge b-brand">评分 ${s.profile.rating}</span></div>
          <div class="muted">${esc(s.profile.address || "")} · 评价数 ${s.profile.rating_count} · 渠道 ${esc(s.profile.pay_channel)}</div>
        </div>
        <div class="section-title">一句话发布余量</div>
        <div class="card">
          <input id="m-text" placeholder="如：面包盲盒 12.9元 原价35元 共3份 今晚20:00-22:00">
          <div class="btn-row"><button class="btn sm" data-action="parse">解析草稿</button></div>
          <div id="parse-result"></div>
        </div>
        <div class="section-title">周边需求（3公里内生效委托）</div>
        <div class="card">
          <div class="kv"><span class="k">生效委托总数</span><span style="font-weight:700">${demand.total_active}</span></div>
          <div class="chips">${Object.entries(demand.by_category || {}).map(([k, v]) => `<span class="chip">${esc(k)} ×${v}</span>`).join("") || '<span class="muted">暂无</span>'}</div>
          <div class="chips">${Object.entries(demand.by_price_band || {}).map(([k, v]) => `<span class="chip warn">${esc(k)} ×${v}</span>`).join("")}</div>
        </div>
        <div class="section-title">我的余量</div>
        ${ls.items.length ? ls.items.map(listingCard).join("") : '<div class="empty">还没有余量，先发布一份。</div>'}
        <div class="section-title">取货核销</div>
        <div class="card">
          <div class="inline-form"><input id="verify-code" placeholder="输入用户 6 位取货码" maxlength="6" inputmode="numeric"><button class="btn green sm" data-action="verify">核销</button></div>
        </div>
        <div class="section-title">今日订单</div>
        ${orders.items.length ? orders.items.map(orderCard).join("") : '<div class="empty">暂无订单。</div>'}
      `;
      if (merchantDraft) renderDraftPreview();
    } catch (e) { page.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  }
  function listingCard(l) {
    const acts = l.status !== "off_shelf"
      ? `<button class="btn ghost sm" data-action="off_shelf" data-lid="${l.id}">下架</button>
         <button class="btn danger sm" data-action="out_of_stock" data-lid="${l.id}">缺货</button>`
      : "";
    return `<div class="card">
      <div class="kv"><span style="font-weight:700">#${l.id} ${esc(l.category)} · ${modeCN(l.mode)}</span>${badge(l.status, L_STATUS)}</div>
      <div class="kv"><span class="price">¥${yuan(l.price)}<small>¥${yuan(l.original_price)}</small></span>
        <span class="muted">可用 ${l.qty_available} · 锁定 ${l.qty_locked} / 共 ${l.qty_total}</span></div>
      <div class="muted">取货窗 ${wallDT(l.pickup_start)} ~ ${wallDT(l.pickup_end)}</div>
      <div class="btn-row">${acts}<button class="btn ghost sm" data-action="snapshot" data-lid="${l.id}">匹配快照</button></div>
    </div>`;
  }
  function orderCard(o) {
    return `<div class="card">
      <div class="kv"><span style="font-weight:700">订单 #${o.id} · 用户 ${o.user_id}</span>${badge(o.status, O_STATUS)}</div>
      <div class="kv"><span class="muted">${esc(o.category)} · ${esc(o.merchant_name)}</span><span class="price">¥${yuan(o.amount)}</span></div>
      ${o.pickup_code ? `<div class="muted">取货码 <b style="color:var(--green);letter-spacing:2px">${esc(o.pickup_code)}</b></div>` : ""}
    </div>`;
  }
  async function merchantParse() {
    const text = $("#m-text").value.trim(); if (!text) { toast("请输入余量描述"); return; }
    const box = $("#parse-result");
    try {
      const d = await api("/api/merchant/listings/parse", { method: "POST", body: { text } });
      if (!d.ok) { merchantDraft = null; box.innerHTML = `<div class="bubble-agent" style="margin-top:10px">🤖 ${esc((d.questions || []).join(" "))}</div>`; }
      else { merchantDraft = d.draft; renderDraftPreview(); }
    } catch (e) { toast(e.message); }
  }
  function renderDraftPreview() {
    const box = $("#parse-result"); if (!box || !merchantDraft) return;
    const d = merchantDraft;
    box.innerHTML = `<div class="reveal" style="margin-top:10px">
      <div class="kv"><span class="k">${esc(d.category)} · ${modeCN(d.mode)}</span><span class="price">¥${yuan(d.price)}<small>¥${yuan(d.original_price)}</small></span></div>
      <div class="muted">共 ${d.qty_total} 份 · 内容 ${esc((d.items || []).join("、"))} · 过敏原 ${esc((d.allergens || []).join("、") || "无")}</div>
      <div class="muted">取货窗 ${wallDT(d.pickup_start)} ~ ${wallDT(d.pickup_end)}</div>
      <div class="btn-row"><button class="btn green sm" data-action="publish-draft">确认发布</button></div>
    </div>`;
  }
  async function merchantPublish() {
    if (!merchantDraft) return;
    try {
      await api("/api/merchant/listings", { method: "POST", body: merchantDraft });
      merchantDraft = null; toast("发布成功，已触发公平匹配"); await mountMerchant();
    } catch (e) { toast(e.message); }
  }
  async function merchantListingAction(lid, act, msg) {
    try { await api(`/api/merchant/listings/${lid}/${act}`, { method: "POST" }); toast(msg); await mountMerchant(); }
    catch (e) { toast(e.message); }
  }
  async function merchantVerify() {
    const code = $("#verify-code").value.trim(); if (!code) { toast("请输入取货码"); return; }
    try {
      const d = await api("/api/merchant/verify-code", { method: "POST", body: { code } });
      toast(d.already ? "重复核销（幂等返回首次结果）" : "核销成功，用户信用 +1");
      $("#verify-code").value = ""; await mountMerchant();
    } catch (e) { toast(e.message); }
  }

  // ---------- 演示控制台抽屉 ----------
  function openDrawer() {
    const drawer = $("#drawer"), mask = $("#drawer-mask");
    drawer.classList.remove("hidden"); mask.classList.remove("hidden");
    mask.onclick = closeDrawer;
    drawer.innerHTML = `
      <button class="drawer-close" id="drawer-close">✕</button>
      <h2>🎛 演示控制台</h2>
      <div class="drawer-sub">仅开发环境 · 虚拟时钟 <span data-clock>--:--:--</span> · 偏移 <span id="d-offset">0</span>s</div>
      <div class="drawer-sec"><h3>时间快进（拨钟 + 立即执行全部任务）</h3>
        <div class="grid4">
          <button class="btn ghost sm" data-ff="180">+3分</button>
          <button class="btn ghost sm" data-ff="2100">+35分</button>
          <button class="btn ghost sm" data-ff="86400">+1天</button>
          <button class="btn ghost sm" id="d-time-reset">重置</button>
        </div>
        <div class="inline-form" style="margin-top:8px"><input id="d-seconds" type="number" placeholder="自定义秒数" min="1"><button class="btn sm" id="d-ff-custom">快进</button></div>
        <div class="btn-row"><button class="btn ghost sm" id="d-jobs">立即执行全部任务</button></div>
      </div>
      <div class="drawer-sec"><h3>一键场景（重置种子并跑完整流程）</h3>
        <div class="grid2">
          <button class="btn green sm" data-scenario="auto">自动付款</button>
          <button class="btn sm" data-scenario="overrun">越界确认</button>
          <button class="btn ghost sm" data-scenario="refund">缺货退款</button>
          <button class="btn danger sm" data-scenario="no_show">未取货惩戒</button>
        </div>
        <div id="drawer-log" class="hidden"></div>
      </div>
      <div class="drawer-sec"><h3>支付失败演示（接下来 n 次自动付失败）</h3>
        <div class="inline-form"><input id="d-failn" type="number" value="1" min="0"><button class="btn sm" id="d-fail">设置 fail-next</button></div>
      </div>
      <div class="drawer-sec"><h3>公平性快照（同 seed 可复现「为什么是他不是我」）</h3>
        <div class="inline-form"><input id="d-lid" type="number" placeholder="余量 listing_id"><button class="btn sm" id="d-snap">查看</button></div>
        <div id="snap-area"></div>
      </div>
      <div class="drawer-sec"><h3>数据</h3>
        <div class="btn-row"><button class="btn ghost sm" id="d-state">全表状态 dump</button><button class="btn danger sm" id="d-seed">重置种子数据</button></div>
        <div id="state-area"></div>
      </div>`;
    $("#drawer-close").onclick = closeDrawer;
    bind(drawer, "[data-ff]", (t) => fastForward(+t.getAttribute("data-ff")));
    bind(drawer, "[data-scenario]", (t) => runScenario(t.getAttribute("data-scenario")));
    $("#d-ff-custom").onclick = () => { const v = +$("#d-seconds").value; if (v > 0) fastForward(v); };
    $("#d-time-reset").onclick = async () => { await api("/api/demo/time/reset", { method: "POST" }); calibrate((await api("/api/feed?since=0&limit=1")).server_now); toast("虚拟时钟已重置"); refreshView(); };
    $("#d-jobs").onclick = async () => { await api("/api/demo/jobs/run", { method: "POST" }); toast("已执行全部任务"); refreshView(); };
    $("#d-fail").onclick = async () => { const n = +$("#d-failn").value || 0; await api("/api/demo/pay/fail-next", { method: "POST", body: { n } }); toast(`fail-next = ${n}`); };
    $("#d-snap").onclick = () => { const lid = +$("#d-lid").value; if (lid > 0) loadSnapshot(lid); };
    $("#d-state").onclick = loadState;
    $("#d-seed").onclick = async () => { await api("/api/demo/seed", { method: "POST" }); chat.cursor = 0; chat.items = []; toast("种子已重置"); refreshView(); };
    updateOffset();
  }
  function closeDrawer() { $("#drawer").classList.add("hidden"); $("#drawer-mask").classList.add("hidden"); }
  function updateOffset() { const el = $("#d-offset"); if (el) el.textContent = Math.round(serverOffset / 1000); }
  function refreshView() { render(); updateOffset(); }

  async function fastForward(seconds) {
    try {
      const d = await api("/api/demo/time/fast-forward", { method: "POST", body: { seconds } });
      calibrate(d.server_now); toast(`已快进 ${seconds} 秒并执行任务`); refreshView();
    } catch (e) { toast(e.message); }
  }
  async function runScenario(name) {
    const log = $("#drawer-log"); log.classList.remove("hidden"); log.innerHTML = `<div class="step">运行场景 ${name} …</div>`;
    try {
      const d = await api(`/api/demo/scenario/${name}`, { method: "POST" });
      log.innerHTML = (d.steps || []).map((s) => `<div class="step">${esc(s)}</div>`).join("");
      chat.cursor = 0; chat.items = [];
      const s = getSession();
      navigate(s && s.role === "merchant" ? "#merchant" : "#chat");
      toast(`场景 ${name} 完成`);
    } catch (e) { log.innerHTML = `<div class="step">失败：${esc(e.message)}</div>`; }
  }
  async function loadSnapshot(lid) {
    const area = $("#snap-area"); if (!area) return;
    $("#d-lid").value = lid;
    area.innerHTML = `<div class="muted">加载中…</div>`;
    try {
      const d = await api(`/api/demo/match-rounds/${lid}`);
      if (!d.rounds.length) { area.innerHTML = `<div class="muted">该余量暂无匹配轮次。</div>`; return; }
      area.innerHTML = d.rounds.map((r) => `
        <div class="card" style="margin-top:8px">
          <div class="kv"><span>轮次 #${r.round_id} ${badge(r.status, { open: ["进行中", "b-green"], exhausted: ["已耗尽", "b-gray"] })}</span><span class="seed-tag">seed ${esc(r.seed)}</span></div>
          <div class="muted">phase_cursor = ${r.phase_cursor}</div>
          <table class="snap"><tr><th>顺位</th><th>用户</th><th>范围内</th><th>权重</th><th>信用/轮换/新客</th><th>越界</th><th>订单</th></tr>
          ${r.candidates.map((c) => `<tr>
            <td>${c.rank}</td><td>${c.user_id}</td><td>${c.in_range ? "✓" : "越界"}</td>
            <td>${c.weight}</td>
            <td>${c.factors.credit_factor} / ${c.factors.rotation_factor} / ${c.factors.new_customer_factor}</td>
            <td>${esc((c.overrun_reasons || []).join(",")) || "—"}</td>
            <td>${c.allocated_order_id || "—"}</td></tr>`).join("")}
          </table>
        </div>`).join("");
    } catch (e) { area.innerHTML = `<div class="muted">${esc(e.message)}</div>`; }
  }
  async function loadState() {
    const area = $("#state-area"); area.innerHTML = `<div class="muted">加载中…</div>`;
    try {
      const d = await api("/api/demo/state");
      const counts = Object.keys(d).filter((k) => Array.isArray(d[k])).map((k) => `${k}:${d[k].length}`).join(" · ");
      area.innerHTML = `<div class="muted" style="margin:6px 0">${esc(counts)}</div><pre class="dump">${esc(JSON.stringify(d, null, 2))}</pre>`;
    } catch (e) { area.innerHTML = `<div class="muted">${esc(e.message)}</div>`; }
  }

  // ---------- 全局计时器（虚拟时钟 + 倒计时）----------
  setInterval(() => {
    const now = virtualNow();
    document.querySelectorAll("[data-clock]").forEach((el) => { el.textContent = wallTime(now); });
    document.querySelectorAll("[data-expires]").forEach((el) => {
      const exp = new Date(el.getAttribute("data-expires")).getTime();
      const rem = exp - now;
      if (rem <= 0) {
        el.textContent = "已超时"; el.classList.add("expired");
        const card = el.closest(".feed-card");
        if (card) card.querySelectorAll("[data-action='confirm'],[data-action='reject']").forEach((b) => { b.disabled = true; });
      } else {
        el.textContent = `${pad(Math.floor(rem / 60000))}:${pad(Math.floor(rem / 1000) % 60)}`;
      }
    });
  }, 1000);

  // ---------- 启动 ----------
  window.addEventListener("hashchange", render);
  (async function init() {
    try { const h = await api("/api/health"); window.__env = h.env; } catch (e) { window.__env = "prod"; }
    if (!location.hash) location.hash = getSession() ? (getSession().role === "merchant" ? "#merchant" : "#chat") : "#login";
    render();
  })();
})();
