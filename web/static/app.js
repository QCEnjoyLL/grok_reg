(() => {
  const __S = window.__S = {"idle": "空闲", "running": "运行中", "ended": "结束", "precheck": "运行前检查：", "no_accounts": "暂无账号", "no_cpa": "暂无 xai-*.json", "email": "邮箱", "password": "密码", "reg_started": "注册任务已启动", "bf_started": "Backfill 已启动", "gen_cpa_started": "已开始为缺失账号生成 CPA", "gen_cpa_one": "已开始生成 CPA（1个）", "stop_req": "已请求停止", "cfg_saved": "配置已保存", "save_fail": "保存失败", "set_saved_token": "设置已保存（Token 已更新）", "set_saved": "设置已保存", "set_fail": "设置保存失败", "quick_saved": "必要配置已保存", "quick_fail": "必要配置保存失败", "save_ok_title": "保存成功", "save_fail_title": "保存失败", "ok_title": "提示", "hint_title": "提示", "run_title": "任务", "err_title": "失败", "cpa_saved": "CPA 配置已保存", "probe_started": "CPA 池测活已启动", "probe_fail": "测活启动失败", "g2a_started": "Grok2API 导入已启动", "g2a_saved": "Grok2API 配置已保存", "g2a_fail": "Grok2API 操作失败"};
  function getToken() { return localStorage.getItem("web_token") || ""; }
  function setToken(t) { localStorage.setItem("web_token", t || ""); }
  function authHeaders(json = true) {
    const h = {};
    if (json) h["Content-Type"] = "application/json";
    const t = getToken();
    if (t) { h["Authorization"] = `Bearer ${t}`; h["X-Web-Token"] = t; }
    return h;
  }
  function withToken(url) {
    const t = getToken();
    if (!t) return url;
    const u = new URL(url, window.location.origin);
    u.searchParams.set("token", t);
    return u.pathname + u.search;
  }
  async function api(path, opts = {}) {
    const timeoutMs = (opts && opts.timeoutMs) || 10000;
    const { timeoutMs: _omit, ...fetchOpts } = opts || {};
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    let res;
    try {
      res = await fetch(path, {
        ...fetchOpts,
        headers: { ...authHeaders(!(fetchOpts.body instanceof FormData)), ...(fetchOpts.headers || {}) },
        credentials: "same-origin",
        signal: ctrl.signal,
      });
    } catch (netErr) {
      clearTimeout(timer);
      const name = (netErr && netErr.name) || "";
      const m = name === "AbortError" ? ("timeout " + timeoutMs + "ms") : String((netErr && netErr.message) || netErr || "network error");
      throw new Error(m + " @ " + path);
    }
    clearTimeout(timer);
    if (res.status === 401) { location.href = "/login"; throw new Error("unauthorized"); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      let detail = data.detail || data.message || res.statusText || res.status;
      if (Array.isArray(detail)) {
        detail = detail.map((x) => (x && (x.msg || x.message || JSON.stringify(x))) || String(x)).join("; ");
      } else if (detail && typeof detail === "object") {
        detail = detail.msg || detail.message || JSON.stringify(detail);
      }
      throw new Error(String(detail) + " @ " + path);
    }
    return data;
  }
  function ensureToastHost() {
    let host = document.getElementById("toast-host");
    if (!host) {
      host = document.createElement("div");
      host.id = "toast-host";
      host.setAttribute("aria-live", "polite");
      document.body.appendChild(host);
    }
    return host;
  }
  function popupToast(msg, type = "ok", title = "") {
    const host = ensureToastHost();
    const el = document.createElement("div");
    el.className = "toast-item " + (type === "err" ? "err" : "ok");
    // Only use explicit title; never default to "保存成功" for non-save actions
    let t = title;
    if (!t) {
      if (type === "err") t = __S.err_title || "失败";
      else t = __S.hint_title || __S.ok_title || "提示";
    }
    el.innerHTML = '<div class="toast-title"></div><div class="toast-msg"></div>';
    el.querySelector(".toast-title").textContent = t;
    el.querySelector(".toast-msg").textContent = String(msg || "");
    // hide empty title bar spacing
    if (!String(t || "").trim()) {
      el.querySelector(".toast-title").style.display = "none";
    }
    host.appendChild(el);
    setTimeout(() => {
      el.style.opacity = "0";
      el.style.transition = "opacity .2s";
      setTimeout(() => el.remove(), 220);
    }, type === "err" ? 4500 : 2800);
  }
  /** log-only toast: no green popup (for progress / job status) */
  function logToast(msg) {
    const log = document.getElementById("log");
    if (log) {
      log.textContent += (log.textContent ? "\n" : "") + "[ui] " + msg;
      log.scrollTop = log.scrollHeight;
    }
  }
  /** soft corner toast without "保存成功" title */
  function toast(msg, type = "ok", title = "") {
    const isSave = title === __S.save_ok_title || title === __S.save_fail_title
      || title === "保存成功" || title === "保存失败";
    if (!title && type === "ok" && !isSave) {
      // progress messages go to log; light toast with 提示
      popupToast(msg, type, __S.hint_title || "提示");
    } else {
      popupToast(msg, type, title || (type === "err" ? (__S.err_title || "失败") : (__S.hint_title || "提示")));
    }
    logToast(msg);
  }
  /** only for real save buttons */
  function toastSave(msg, ok = true) {
    popupToast(msg, ok ? "ok" : "err", ok ? (__S.save_ok_title || "保存成功") : (__S.save_fail_title || "保存失败"));
    logToast(msg);
  }
  function setBadge(job) {
    const el = document.getElementById("run-badge");
    if (!el) return;
    if (job && job.running) {
      el.className = "badge run";
      el.textContent = __S.running + " \u00b7 " + (job.kind || "job");
    } else if (job && job.exit_code != null && job.exit_code !== 0) {
      el.className = "badge fail";
      el.textContent = __S.ended + " code=" + job.exit_code;
    } else {
      el.className = "badge idle";
      el.textContent = __S.idle;
    }
  }
  function appendLines(lines) {
    if (!lines || !lines.length) return;
    const log = document.getElementById("log");
    log.textContent += (log.textContent ? "\n" : "") + lines.join("\n");
    log.scrollTop = log.scrollHeight;
  }
  function esc(s) {
    return String(s ?? "")
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
  }
  function applyNovncUrl(url) {
    const a = document.getElementById("link-novnc");
    const prev = document.getElementById("novnc-preview");
    if (a && url) a.href = url;
    if (prev && url) { prev.href = url; prev.textContent = url; }
  }
  function setVal(id, v) {
    const el = document.getElementById(id);
    if (!el || v === undefined || v === null) return;
    el.value = String(v);
  }
  function getVal(id) {
    const el = document.getElementById(id);
    return el ? String(el.value || "").trim() : "";
  }
  const MAIL_HINTS = {
    moemail: "MoeMail：填密钥和接口地址；域名可空自动获取，或多个逗号分隔随机。",
    cloudflare: "临时邮箱：接口地址填 Worker（xxx.workers.dev）。域名可空（自动获取）或多个逗号分隔随机抽取。",
    cloudmail: "CloudMail：填管理端地址、管理员邮箱与密码；域名写在邮箱域名。",
    duckmail: "DuckMail：填密钥即可。",
    yyds: "YYDS：填密钥和/或 JWT。",
  };
  function syncMailProviderFields() {
    const provider = (getVal("q-email_provider") || "moemail").toLowerCase();
    document.querySelectorAll("[data-mail]").forEach((el) => {
      const raw = String(el.getAttribute("data-mail") || "");
      const list = raw.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
      const show = list.includes(provider);
      el.hidden = !show;
      if (el.tagName === "DETAILS" && !show) el.open = false;
    });
    const hint = document.getElementById("quick-mail-hint");
    if (hint) hint.textContent = MAIL_HINTS[provider] || "选择邮箱渠道后，下方只显示该渠道需要的配置项。";
    const domainTitle = document.getElementById("defaultDomains-title");
    const input = document.getElementById("q-defaultDomains");
    if (domainTitle) {
      if (provider === "cloudflare" || provider === "cloudmail") {
        domainTitle.textContent = "邮箱域名（必填）";
      } else {
        domainTitle.textContent = "邮箱域名（可选）";
      }
    }
    if (input) {
      if (provider === "cloudflare") input.placeholder = "temps.cc.cd,piv.cc.cd（可多个，随机）";
      else if (provider === "cloudmail") input.placeholder = "a.com,b.com（可多个，随机）";
      else input.placeholder = "a.com,b.com（可多个，随机；空则自动）";
    }
  }
  function fillQuickFromConfig(cfg) {
    if (!cfg) return;
    setVal("q-email_provider", cfg.email_provider || "moemail");
    setVal("q-moemail_api_base", cfg.moemail_api_base || "https://mail.nloln.cn");
    setVal("q-moemail_api_key", cfg.moemail_api_key || "");
    setVal("q-defaultDomains", cfg.defaultDomains || "");
    setVal("q-cloudflare_api_base", cfg.cloudflare_api_base || "");
    setVal("q-cloudflare_api_key", cfg.cloudflare_api_key || "");
    setVal("q-cloudflare_auth_mode", cfg.cloudflare_auth_mode || "x-admin-auth");
    setVal("q-cloudflare_path_accounts", cfg.cloudflare_path_accounts || "/api/new_address");
    setVal("q-cloudflare_path_messages", cfg.cloudflare_path_messages || "/api/mails");
    setVal("q-cloudflare_path_domains", cfg.cloudflare_path_domains || "/api/domains");
    setVal("q-cloudflare_path_token", cfg.cloudflare_path_token || "/api/token");
    setVal("q-proxy", cfg.proxy || "");
    setVal("q-cpa_proxy", cfg.cpa_proxy || "");
    setVal("q-cpa_export_enabled", String(cfg.cpa_export_enabled !== false));
    setVal("q-cpa_headless", String(!!cfg.cpa_headless));
    setVal("q-cpa_base_url", cfg.cpa_base_url || "https://cli-chat-proxy.grok.com/v1");
    setVal("q-cpa_auth_dir", cfg.cpa_auth_dir || "./cpa_auths");
    setVal("q-cpa_management_upload_enabled", String(!!cfg.cpa_management_upload_enabled));
    setVal("q-cpa_management_base", cfg.cpa_management_base || "");
    setVal("q-cpa_management_key", cfg.cpa_management_key || "");
    setVal("q-cpa_probe_usability", String(cfg.cpa_probe_usability !== false));
    setVal("q-cpa_delete_unusable", String(cfg.cpa_delete_unusable !== false));
    setVal("q-local_turnstile_enabled", String(!!cfg.local_turnstile_enabled));
    setVal("q-local_turnstile_url", cfg.local_turnstile_url || "http://127.0.0.1:5072");
    setVal("q-grok2api_auto_add_remote", String(!!cfg.grok2api_auto_add_remote));
    setVal("q-grok2api_pool_name", cfg.grok2api_pool_name || "ssoBasic");
    setVal("q-grok2api_import_mode", cfg.grok2api_import_mode || "tokens_add");
    setVal("q-grok2api_remote_base", cfg.grok2api_remote_base || "");
    setVal("q-grok2api_remote_app_key", cfg.grok2api_remote_app_key || "");
    setVal("q-grok2api_admin_username", cfg.grok2api_admin_username || "admin");
    setVal("q-grok2api_admin_password", cfg.grok2api_admin_password || "");
    setVal("q-grok2api_import_batch_size", String(cfg.grok2api_import_batch_size || 50));
    setVal("q-cloudmail_url", cfg.cloudmail_url || "");
    setVal("q-cloudmail_admin_email", cfg.cloudmail_admin_email || "");
    setVal("q-cloudmail_password", cfg.cloudmail_password || "");
    setVal("q-duckmail_api_key", cfg.duckmail_api_key || "");
    setVal("q-yyds_api_key", cfg.yyds_api_key || "");
    setVal("q-yyds_jwt", cfg.yyds_jwt || "");
    syncMailProviderFields();
  }
  function collectQuickConfig() {
    const bool = (v) => v === "true" || v === true;
    const out = {
      email_provider: getVal("q-email_provider") || "moemail",
      moemail_api_base: getVal("q-moemail_api_base") || "https://mail.nloln.cn",
      defaultDomains: getVal("q-defaultDomains"),
      cloudflare_api_base: getVal("q-cloudflare_api_base"),
      cloudflare_auth_mode: getVal("q-cloudflare_auth_mode") || "x-admin-auth",
      cloudflare_path_accounts: getVal("q-cloudflare_path_accounts") || "/api/new_address",
      cloudflare_path_messages: getVal("q-cloudflare_path_messages") || "/api/mails",
      cloudflare_path_domains: getVal("q-cloudflare_path_domains") || "/api/domains",
      cloudflare_path_token: getVal("q-cloudflare_path_token") || "/api/token",
      cpa_export_enabled: bool(getVal("q-cpa_export_enabled") || "true"),
      cpa_headless: bool(getVal("q-cpa_headless") || "false"),
      cpa_base_url: getVal("q-cpa_base_url") || "https://cli-chat-proxy.grok.com/v1",
      cpa_auth_dir: getVal("q-cpa_auth_dir") || "./cpa_auths",
      cpa_management_upload_enabled: bool(getVal("q-cpa_management_upload_enabled") || "false"),
      cpa_management_base: getVal("q-cpa_management_base"),
      cpa_probe_usability: bool(getVal("q-cpa_probe_usability") || "true"),
      cpa_delete_unusable: bool(getVal("q-cpa_delete_unusable") || "true"),
      local_turnstile_enabled: bool(getVal("q-local_turnstile_enabled") || "false"),
      local_turnstile_url: getVal("q-local_turnstile_url") || "http://127.0.0.1:5072",
      grok2api_auto_add_remote: bool(getVal("q-grok2api_auto_add_remote") || "false"),
      grok2api_pool_name: getVal("q-grok2api_pool_name") || "ssoBasic",
      grok2api_import_mode: getVal("q-grok2api_import_mode") || "tokens_add",
      grok2api_remote_base: getVal("q-grok2api_remote_base") || "",
      grok2api_admin_username: getVal("q-grok2api_admin_username") || "admin",
      grok2api_import_batch_size: Number(getVal("q-grok2api_import_batch_size") || 50),
      cloudmail_url: getVal("q-cloudmail_url"),
      cloudmail_admin_email: getVal("q-cloudmail_admin_email"),
    };
    const key = getVal("q-cloudflare_api_key");
    if (key && !key.includes("*")) out.cloudflare_api_key = key;
    const mkey = getVal("q-moemail_api_key");
    if (mkey && !mkey.includes("*")) out.moemail_api_key = mkey;
    const dkey = getVal("q-duckmail_api_key");
    if (dkey && !dkey.includes("*")) out.duckmail_api_key = dkey;
    const ykey = getVal("q-yyds_api_key");
    if (ykey && !ykey.includes("*")) out.yyds_api_key = ykey;
    const yjwt = getVal("q-yyds_jwt");
    if (yjwt && !yjwt.includes("*")) out.yyds_jwt = yjwt;
    const cpaKey = getVal("q-cpa_management_key");
    if (cpaKey && !cpaKey.includes("*")) out.cpa_management_key = cpaKey;
    const g2aKey = getVal("q-grok2api_remote_app_key");
    if (g2aKey && !g2aKey.includes("*")) out.grok2api_remote_app_key = g2aKey;
    const g2aPwd = getVal("q-grok2api_admin_password");
    if (g2aPwd && !g2aPwd.includes("*")) out.grok2api_admin_password = g2aPwd;
    const cmp = getVal("q-cloudmail_password");
    if (cmp && !cmp.includes("*")) out.cloudmail_password = cmp;
    // proxy URLs are redacted in UI; only write back when user actually changed them
    const proxyVal = getVal("q-proxy");
    if (proxyVal && !proxyVal.includes("*")) out.proxy = proxyVal;
    else if (proxyVal === "") out.proxy = "";
    const cpaProxyVal = getVal("q-cpa_proxy");
    if (cpaProxyVal && !cpaProxyVal.includes("*")) out.cpa_proxy = cpaProxyVal;
    else if (cpaProxyVal === "") out.cpa_proxy = "";
    return out;
  }
  async function refreshStatus() {
    const st = await api("/api/status");
    document.getElementById("st-accounts").textContent = st.accounts_count;
    document.getElementById("st-cpa").textContent = st.cpa_count;
    document.getElementById("st-mail").textContent = st.email_provider || "-";
    document.getElementById("st-display").textContent = st.display || "-";
    setBadge(st.job);
    if (st.novnc_url) applyNovncUrl(st.novnc_url);
    document.getElementById("link-accounts").href = withToken("/api/download/accounts");
    const linkAccArch = document.getElementById("link-accounts-archive");
    if (linkAccArch) linkAccArch.href = withToken("/api/download/accounts-archive");
    const linkCpaArch = document.getElementById("link-cpa-archive");
    if (linkCpaArch) linkCpaArch.href = withToken("/api/download/cpa-archive");
    const linkCpa = document.getElementById("link-cpa");
    if (linkCpa) linkCpa.href = withToken("/api/download/cpa");
    const linkCpa2 = document.getElementById("link-cpa-2");
    if (linkCpa2) linkCpa2.href = withToken("/api/download/cpa");
    const box = document.getElementById("setup-hints");
    if (box && st.setup_hints) {
      if (st.setup_hints.length) {
        box.hidden = false;
        box.innerHTML = "<strong>" + esc(__S.precheck) + "</strong> " + st.setup_hints.map(esc).join(" · ");
      } else box.hidden = true;
    }
    return st;
  }
  const PAGE_SIZES = [5, 10, 20, 50, 100, 500];
  const listState = {
    accounts: { page: 1, pageSize: 10, total: 0, pages: 1, filterSso: "all", filterCpa: "all", filterQ: "" },
    cpa: { page: 1, pageSize: 10, total: 0, pages: 1, filterStatus: "all", filterQ: "" },
  };
  function loadPageSize(key, fallback) {
    try {
      const v = parseInt(localStorage.getItem("grok_reg_" + key + "_page_size") || "", 10);
      if (PAGE_SIZES.includes(v)) return v;
    } catch (_) {}
    return fallback;
  }
  function savePageSize(key, n) {
    try { localStorage.setItem("grok_reg_" + key + "_page_size", String(n)); } catch (_) {}
  }
  listState.accounts.pageSize = loadPageSize("accounts", 10);
  listState.cpa.pageSize = loadPageSize("cpa", 10);

  function updatePagerUI(kind, meta) {
    const st = listState[kind];
    st.total = Number(meta.total || 0);
    st.page = Number(meta.page || st.page || 1);
    st.pages = Number(meta.pages || 1);
    st.pageSize = Number(meta.limit || st.pageSize || 10);
    const info = document.getElementById(kind + "-pager-info");
    const prev = document.getElementById(kind + "-prev");
    const next = document.getElementById(kind + "-next");
    const sel = document.getElementById(kind + "-page-size");
    if (sel && String(sel.value) !== String(st.pageSize)) sel.value = String(st.pageSize);
    const start = st.total ? ((st.page - 1) * st.pageSize + 1) : 0;
    const end = Math.min(st.page * st.pageSize, st.total);
    if (info) {
      info.textContent = st.total
        ? ("共 " + st.total + " 条 · 第 " + st.page + "/" + st.pages + " 页 · 显示 " + start + "-" + end)
        : "共 0 条";
    }
    if (prev) prev.disabled = st.page <= 1 || st.total === 0;
    if (next) next.disabled = st.page >= st.pages || st.total === 0;
  }

  async function refreshAccounts() {
    const st = listState.accounts;
    const limit = st.pageSize;
    const offset = (st.page - 1) * limit;
    const ssoEl = document.getElementById("accounts-filter-sso");
    const cpaEl = document.getElementById("accounts-filter-cpa");
    const qEl = document.getElementById("accounts-filter-q");
    if (ssoEl) st.filterSso = ssoEl.value || "all";
    if (cpaEl) st.filterCpa = cpaEl.value || "all";
    if (qEl) st.filterQ = String(qEl.value || "").trim();
    const qs = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
      sso: st.filterSso || "all",
      cpa: st.filterCpa || "all",
      q: st.filterQ || "",
    });
    const data = await api("/api/accounts?" + qs.toString());
    const box = document.getElementById("accounts-table");
    updatePagerUI("accounts", data);
    const stats = document.getElementById("accounts-filter-stats");
    if (stats) {
      const all = data.total_all != null ? data.total_all : data.total;
      const miss = data.without_cpa != null ? data.without_cpa : "-";
      const has = data.with_cpa != null ? data.with_cpa : "-";
      stats.textContent = "匹配 " + (data.total || 0) + " / 全部 " + all + " · 缺CPA " + miss + " · 有CPA " + has;
    }
    if (!data.items || !data.items.length) {
      box.innerHTML = "<p class='hint'>" + esc(__S.no_accounts) + "</p>";
      return;
    }
    const rows = data.items.map((r) => {
      const pass = String(r.password || "");
      const masked = pass ? "•".repeat(Math.min(Math.max(pass.length, 8), 24)) : "-";
      const passCell = pass
        ? (
          '<span class="secret-cell">' +
            '<code class="secret-text" data-secret="' + esc(pass) + '" data-shown="0">' + masked + '</code>' +
            '<button type="button" class="btn-eye" title="显示密码" aria-label="显示或隐藏密码">👁</button>' +
          '</span>'
        )
        : "-";
      const cpaBadge = r.has_cpa
        ? '<span class="badge-up yes">有CPA</span>'
        : '<span class="badge-up no">缺CPA</span>';
      const email = String(r.email || "");
      const delBtn =
        '<button type="button" class="btn ghost sm btn-account-del" data-email="' +
        esc(email) +
        '" data-has-cpa="' +
        (r.has_cpa ? "1" : "0") +
        '" title="从 accounts_cli.txt 删除该账号">删除</button>';
      return (
        "<tr>" +
        '<td class="col-email" title="' + esc(email) + '">' + esc(email) + "</td>" +
        '<td class="col-pass">' + passCell + "</td>" +
        "<td>" + (r.has_sso ? esc(r.sso_preview || "有") : "-") + "</td>" +
        '<td class="col-cpa-flag">' + cpaBadge + "</td>" +
        '<td class="col-acc-act"><div class="col-act-btns">' + delBtn + "</div></td>" +
        "</tr>"
      );
    }).join("");
    box.innerHTML =
      "<table>" +
      '<colgroup><col class="col-email"><col class="col-pass"><col class="col-sso"><col class="col-cpa-flag"><col class="col-acc-act"></colgroup>' +
      "<thead><tr>" +
      "<th>" + esc(__S.email || "邮箱") + "</th>" +
      "<th>" + esc(__S.password || "密码") + "</th>" +
      "<th>SSO</th>" +
      "<th>CPA</th>" +
      "<th>操作</th>" +
      "</tr></thead>" +
      "<tbody>" + rows + "</tbody>" +
      "</table>";
  }
  async function refreshCpa() {
    const st = listState.cpa;
    const limit = st.pageSize;
    const offset = (st.page - 1) * limit;
    const statusEl = document.getElementById("cpa-filter-status");
    const qEl = document.getElementById("cpa-filter-q");
    if (statusEl) st.filterStatus = statusEl.value || "all";
    if (qEl) st.filterQ = String(qEl.value || "").trim();
    const qs = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
      status: st.filterStatus || "all",
      q: st.filterQ || "",
    });
    const data = await api("/api/cpa?" + qs.toString());
    const box = document.getElementById("cpa-table");
    updatePagerUI("cpa", data);
    const stats = document.getElementById("cpa-filter-stats");
    if (stats) {
      const all = data.total_all != null ? data.total_all : data.total;
      const up = data.uploaded_count != null ? data.uploaded_count : "-";
      const pe = data.pending_count != null ? data.pending_count : "-";
      stats.textContent = "共 " + all + " · 已上传 " + up + " · 未上传 " + pe + " · 筛选 " + (data.total || 0);
    }
    if (!data.items || !data.items.length) {
      box.innerHTML = "<p class='hint'>" + esc(__S.no_cpa || "暂无 CPA 文件") + "</p>";
      return;
    }
    const rows = data.items.map((r) => {
      const file = r.file || ("xai-" + (r.email || "") + ".json");
      const href = withToken("/api/download/cpa/" + encodeURIComponent(file));
      const uploaded = !!r.uploaded;
      const badge = uploaded
        ? '<span class="badge-up yes">已上传</span>'
        : '<span class="badge-up no">未上传</span>';
      const upAt = uploaded ? esc(r.uploaded_at || "-") : "-";
      const mtime = esc(r.mtime || "-");
      const size = (r.size == null || r.size === "") ? "-" : String(r.size);
      const markBtn = uploaded
        ? ""
        : ('<button type="button" class="btn ghost sm btn-cpa-mark-one" data-file="' + esc(file) + '">标记</button>');
      return (
        "<tr>" +
        '<td class="col-email" title="' + esc(r.email || "") + '">' + esc(r.email || "") + "</td>" +
        '<td class="col-upload">' + badge + "</td>" +
        '<td class="col-upload-at">' + upAt + "</td>" +
        '<td class="col-mtime">' + mtime + "</td>" +
        '<td class="col-size">' + size + "</td>" +
        '<td class="col-act"><div class="col-act-btns">' +
          '<button type="button" class="btn sm btn-cpa-upload-one" data-file="' + esc(file) + '" data-uploaded="' + (uploaded ? "1" : "0") + '">' + (uploaded ? "重传" : "上传") + "</button>" +
          markBtn +
          '<a class="btn ghost sm" href="' + href + '" download="' + esc(file) + '">下载</a>' +
        "</div></td>" +
        "</tr>"
      );
    }).join("");
    box.innerHTML =
      '<table class="cpa-table">' +
      "<colgroup>" +
      '<col class="col-email">' +
      '<col class="col-upload">' +
      '<col class="col-upload-at">' +
      '<col class="col-mtime">' +
      '<col class="col-size">' +
      '<col class="col-act">' +
      "</colgroup>" +
      "<thead><tr>" +
      "<th>邮箱</th>" +
      "<th>上传状态</th>" +
      "<th>上传时间</th>" +
      "<th>修改时间</th>" +
      "<th>大小</th>" +
      '<th class="col-act">操作</th>' +
      "</tr></thead>" +
      "<tbody>" + rows + "</tbody>" +
      "</table>";
  }
  async function refreshConfig() {
    const data = await api("/api/config?redact=true");
    document.getElementById("config-editor").value = JSON.stringify(data.config || {}, null, 2);
    fillQuickFromConfig(data.config || {});
  }
  async function refreshSettings() {
    const s = await api("/api/settings");
    document.getElementById("token-hint").textContent = s.web_token_hint || "-";
    document.getElementById("set-novnc-url").value = s.novnc_public_url || "";
    document.getElementById("set-novnc-host").value = s.novnc_host || "";
    document.getElementById("set-novnc-port").value = s.novnc_port || "";
    if (s.novnc_url) applyNovncUrl(s.novnc_url);
  }
  let __ws = null;
  let __wsWaiters = [];

  function connectWs() {
    const t = getToken();
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const q = t ? `?token=${encodeURIComponent(t)}` : "";
    const ws = new WebSocket(`${proto}://${location.host}/ws/logs${q}`);
    __ws = ws;
    ws.onopen = () => {
      try { ws.send(JSON.stringify({ op: "hello" })); } catch (_) {}
    };
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.reset) {
          const log = document.getElementById("log");
          if (log) log.textContent = (msg.lines && msg.lines.length) ? msg.lines.join("\n") : "";
        } else if (msg.lines && msg.lines.length) {
          appendLines(msg.lines);
        }
        if (msg.status) setBadge(msg.status);
        // resolve pending command waiters
        if (msg.type === "ack" || msg.type === "error") {
          const waiters = __wsWaiters.slice();
          __wsWaiters = [];
          waiters.forEach((w) => {
            try { w(msg); } catch (_) {}
          });
        }
      } catch (_) {}
    };
    ws.onclose = () => {
      __ws = null;
      const waiters = __wsWaiters.slice();
      __wsWaiters = [];
      waiters.forEach((w) => {
        try { w({ type: "error", detail: "websocket closed" }); } catch (_) {}
      });
      setTimeout(connectWs, 2500);
    };
  }

  function wsSend(op, payload = {}, timeoutMs = 8000) {
    return new Promise((resolve, reject) => {
      if (!__ws || __ws.readyState !== WebSocket.OPEN) {
        reject(new Error("websocket not ready"));
        return;
      }
      const timer = setTimeout(() => {
        __wsWaiters = __wsWaiters.filter((w) => w !== onMsg);
        reject(new Error("websocket timeout"));
      }, timeoutMs);
      function onMsg(msg) {
        clearTimeout(timer);
        if (msg.type === "error") reject(new Error(msg.detail || "ws error"));
        else resolve(msg);
      }
      __wsWaiters.push(onMsg);
      try {
        __ws.send(JSON.stringify({ op, ...payload }));
      } catch (err) {
        clearTimeout(timer);
        __wsWaiters = __wsWaiters.filter((w) => w !== onMsg);
        reject(err);
      }
    });
  }

  function ensureJobFrame() {
    let f = document.getElementById("job-frame");
    if (!f) {
      f = document.createElement("iframe");
      f.id = "job-frame";
      f.name = "job-frame";
      f.style.cssText = "position:absolute;width:0;height:0;border:0;left:-9999px;top:-9999px;";
      document.body.appendChild(f);
    }
    return f;
  }

  function startViaHiddenGet(params) {
    return new Promise((resolve, reject) => {
      ensureJobFrame();
      const q = new URLSearchParams(params);
      const t = getToken();
      if (t) q.set("token", t);
      // prefer status/do then go
      const urls = [
        "/api/status/do?" + q.toString(),
        "/api/go?" + q.toString(),
      ];
      let i = 0;
      const tryNext = () => {
        if (i >= urls.length) {
          reject(new Error("iframe GET failed"));
          return;
        }
        const url = urls[i++];
        toast("[ui] iframe GET " + url.split("?")[0] + " ...");
        const form = document.createElement("form");
        form.method = "GET";
        form.action = url.split("?")[0];
        form.target = "job-frame";
        form.style.display = "none";
        const qs = new URLSearchParams(url.split("?")[1] || "");
        qs.forEach((v, k) => {
          const input = document.createElement("input");
          input.type = "hidden";
          input.name = k;
          input.value = v;
          form.appendChild(input);
        });
        document.body.appendChild(form);
        form.submit();
        form.remove();
        // poll status after short delay
        let tries = 0;
        const poll = async () => {
          tries += 1;
          try {
            const st = await api("/api/status", { timeoutMs: 5000 });
            if (st && st.job && st.job.running) {
              resolve({ ok: true, job: st.job, via: "iframe-get" });
              return;
            }
          } catch (_) {}
          if (tries >= 6) {
            tryNext();
            return;
          }
          setTimeout(poll, 700);
        };
        setTimeout(poll, 600);
      };
      tryNext();
    });
  }

  function isNetErr(err) {
    const m = String((err && err.message) || err || "").toLowerCase();
    return (
      m.includes("failed to fetch") ||
      m.includes("network") ||
      m.includes("websocket") ||
      m.includes("timeout") ||
      m.includes("abort") ||
      m.includes("load failed") ||
      m.includes("404") ||
      m.includes("not found") ||
      m.includes("502") ||
      m.includes("503") ||
      m.includes("504")
    );
  }

  async function startJob(body) {
    const extra = Number(body.extra || 1);
    const threads = Number(body.threads || 1);
    const mint_workers = Number(body.mint_workers ?? -1);
    const fast = body.fast !== false;
    const payload = { action: "start", extra, threads, mint_workers, fast };
    const errors = [];

    async function tryChannel(name, fn, requireJob = true) {
      logToast(name + " ...");
      try {
        const res = await fn();
        if (!res) {
          errors.push(name + ": empty response");
          return null;
        }
        const hasJob = !!(res.job || res.job_result || res.queued || res.cmd || (res.job && res.job.pid));
        if (requireJob && !hasJob && res.ok) {
          // e.g. old image accepted PUT /api/config but ignored _cmd
          errors.push(name + ": no job in response");
          logToast(name + " no job field, try next");
          return null;
        }
        if (res.ok || hasJob) return res.job_result || res;
        errors.push(name + ": unexpected response");
        return null;
      } catch (err) {
        const msg = String(err.message || err);
        errors.push(name + ": " + msg);
        logToast(name + " failed: " + msg);
        return null;
      }
    }

    // 1) PUT /api/config + _cmd (known-good path)
    let res = await tryChannel("PUT /api/config _cmd", () =>
      api("/api/config", {
        method: "PUT",
        timeoutMs: 12000,
        body: JSON.stringify({ config: { _cmd: payload } }),
      })
    );
    if (res) return res;

    // 2) GET /api/status  (probe) then GET /api/status/do
    res = await tryChannel("GET /api/status/do", () => {
      const q = new URLSearchParams({
        action: "start",
        extra: String(extra),
        threads: String(threads),
        mint_workers: String(mint_workers),
        fast: fast ? "1" : "0",
      });
      return api("/api/status/do?" + q.toString(), { timeoutMs: 8000 });
    });
    if (res) return res;

    // 3) GET /api/go
    res = await tryChannel("GET /api/go", () => {
      const q = new URLSearchParams({
        action: "start",
        extra: String(extra),
        threads: String(threads),
        mint_workers: String(mint_workers),
        fast: fast ? "1" : "0",
      });
      return api("/api/go?" + q.toString(), { timeoutMs: 8000 });
    });
    if (res) return res;

    // 4) PUT /api/task
    res = await tryChannel("PUT /api/task", () =>
      api("/api/task", {
        method: "PUT",
        timeoutMs: 8000,
        body: JSON.stringify(payload),
      })
    );
    if (res) return res;

    // 5) WebSocket
    res = await tryChannel("WebSocket start", () => wsSend("start", payload, 8000));
    if (res) return res;

    // 6) legacy POST
    res = await tryChannel("POST /api/jobs/start", () =>
      api("/api/jobs/start", {
        method: "POST",
        timeoutMs: 8000,
        body: JSON.stringify({ extra, threads, mint_workers, fast }),
      })
    );
    if (res) return res;

    throw new Error("all start channels failed: " + errors.join(" | "));
  }

  async function stopJob() {
    const errors = [];
    const channels = [
      ["PUT /api/config _cmd", () =>
        api("/api/config", {
          method: "PUT",
          timeoutMs: 8000,
          body: JSON.stringify({ config: { _cmd: { action: "stop" } } }),
        })],
      ["GET /api/go?action=stop", () => api("/api/go?action=stop", { timeoutMs: 8000 })],
      ["WebSocket stop", () => wsSend("stop", {}, 5000)],
      ["POST /api/jobs/stop", () => api("/api/jobs/stop", { method: "POST", body: "{}", timeoutMs: 8000 })],
    ];
    for (const [name, fn] of channels) {
      try {
        toast("[ui] stop via " + name);
        return await fn();
      } catch (err) {
        errors.push(name + ": " + (err.message || err));
      }
    }
    throw new Error("stop failed: " + errors.join(" | "));
  }

  document.getElementById("form-register").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const btn = form.querySelector('button[type="submit"]');
    if (btn) { btn.disabled = true; btn.dataset.prev = btn.textContent; btn.textContent = "启动中..."; }
    const fd = new FormData(form);
    const body = {
      extra: Number(fd.get("extra") || 1),
      threads: Number(fd.get("threads") || 1),
      mint_workers: Number(fd.get("mint_workers") || -1),
      fast: fd.get("fast") === "on",
    };
    try {
      const res = await startJob(body);
      toast(__S.reg_started + " pid=" + ((res.job && res.job.pid) || "?"));
      await refreshStatus();
      const log = document.getElementById("log");
      if (log) log.scrollTop = log.scrollHeight;
    } catch (err) {
      const msg = String(err.message || err);
      toast("start failed: " + msg, "err");
      alert("start failed: " + msg);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = btn.dataset.prev || "开始注册"; }
    }
  });

  async function startBackfill(opts = {}) {
    const body = {
      action: "backfill",
      limit: Number(opts.limit ?? 0),
      email: String(opts.email || ""),
      timeout: Number(opts.timeout || 300),
      probe: opts.probe !== false,
      headless: !!opts.headless,
      sleep: Number(opts.sleep || 3),
    };
    // Prefer channels that work in this deployment
    try {
      return await api("/api/config", {
        method: "PUT",
        timeoutMs: 12000,
        body: JSON.stringify({ config: { _cmd: body } }),
      });
    } catch (e1) {
      try {
        return await api("/api/task", {
          method: "PUT",
          timeoutMs: 12000,
          body: JSON.stringify(body),
        });
      } catch (e2) {
        try {
          return await api("/api/go?" + new URLSearchParams({
            action: "backfill",
            limit: String(body.limit),
            email: body.email,
            timeout: String(body.timeout),
            probe: body.probe ? "1" : "0",
            headless: body.headless ? "1" : "0",
          }), { timeoutMs: 12000 });
        } catch (e3) {
          return await api("/api/jobs/backfill", {
            method: "POST",
            timeoutMs: 12000,
            body: JSON.stringify(body),
          });
        }
      }
    }
  }

  async function ensureCpaExportEnabled() {
    try {
      const data = await api("/api/config?redact=true");
      const cfg = data.config || {};
      let on = cfg.cpa_export_enabled;
      if (typeof on === "string") on = ["true", "1", "yes", "on"].includes(on.toLowerCase());
      if (on === false || on === 0) {
        await api("/api/config", {
          method: "PUT",
          body: JSON.stringify({ config: { cpa_export_enabled: true, cpa_headless: false } }),
        });
        toast("[ui] 已自动打开 cpa_export_enabled=true");
        await refreshConfig();
      }
    } catch (err) {
      toast("[ui] 检查 CPA 开关失败: " + (err.message || err), "err");
    }
  }

  
  async function startProbePool(opts = {}) {
    const body = {
      limit: opts.limit != null ? Number(opts.limit) : 0,
      email: String(opts.email || ""),
      workers: opts.workers != null ? Number(opts.workers) : 4,
      delete: opts.delete !== false,
      offset: opts.offset != null ? Number(opts.offset) : 0,
      sleep: opts.sleep != null ? Number(opts.sleep) : 0,
    };
    // Prefer dedicated endpoint; fall back to unified task action.
    try {
      return await api("/api/jobs/probe-pool", {
        method: "POST",
        body: JSON.stringify(body),
        timeoutMs: 15000,
      });
    } catch (e1) {
      try {
        return await api("/api/task", {
          method: "POST",
          body: JSON.stringify({ action: "probe_pool", ...body }),
          timeoutMs: 15000,
        });
      } catch (e2) {
        throw e1;
      }
    }
  }

  
  function collectGrok2ApiPatch() {
    const patch = collectQuickConfig();
    const out = {
      grok2api_auto_add_remote: patch.grok2api_auto_add_remote,
      grok2api_pool_name: patch.grok2api_pool_name,
      grok2api_import_mode: patch.grok2api_import_mode,
      grok2api_remote_base: patch.grok2api_remote_base,
      grok2api_admin_username: patch.grok2api_admin_username,
      grok2api_import_batch_size: patch.grok2api_import_batch_size,
    };
    if (patch.grok2api_remote_app_key) out.grok2api_remote_app_key = patch.grok2api_remote_app_key;
    if (patch.grok2api_admin_password) out.grok2api_admin_password = patch.grok2api_admin_password;
    return out;
  }

  document.getElementById("btn-save-grok2api")?.addEventListener("click", async () => {
    try {
      const patch = collectGrok2ApiPatch();
      await api("/api/config", { method: "PUT", body: JSON.stringify({ config: patch }) });
      toastSave(__S.g2a_saved || "Grok2API 配置已保存", true);
      await refreshConfig();
    } catch (err) {
      toastSave((__S.g2a_fail || "Grok2API 操作失败") + ": " + (err.message || err), false);
    }
  });

  document.getElementById("btn-test-grok2api")?.addEventListener("click", async () => {
    try {
      try {
        await api("/api/config", { method: "PUT", body: JSON.stringify({ config: collectGrok2ApiPatch() }) });
      } catch (_) {}
      const mode = getVal("q-grok2api_import_mode") || "tokens_add";
      const res = await api("/api/grok2api/test", {
        method: "POST",
        body: JSON.stringify({ mode }),
        timeoutMs: 20000,
      });
      toast("Grok2API 连接成功 mode=" + (res.mode || mode) + " base=" + (res.base_url || ""), "ok");
    } catch (err) {
      toast((__S.g2a_fail || "Grok2API 操作失败") + ": " + (err.message || err), "err");
    }
  });

  document.getElementById("btn-import-grok2api")?.addEventListener("click", async () => {
    const btn = document.getElementById("btn-import-grok2api");
    if (btn) { btn.disabled = true; btn.dataset.prev = btn.textContent; btn.textContent = "导入中..."; }
    try {
      const mode = getVal("q-grok2api_import_mode") || "tokens_add";
      const ok = confirm(
        "确认把当前账号列表中的 SSO 导入 Grok2API？\n\n" +
        "模式: " + mode + "\n" +
        "请先在「系统设置 → Grok2API 导入配置」填好地址/密钥。\n" +
        "进度看实时日志，可点停止。"
      );
      if (!ok) return;
      toast("[ui] 启动 Grok2API 导入 ...");
      const res = await api("/api/grok2api/import", {
        method: "POST",
        body: JSON.stringify({ mode, limit: 0 }),
        timeoutMs: 15000,
      });
      toast((__S.g2a_started || "Grok2API 导入已启动") + " pid=" + ((res.job && res.job.pid) || "?"));
      refreshStatus();
    } catch (err) {
      toast((__S.g2a_fail || "Grok2API 操作失败") + ": " + (err.message || err), "err");
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = btn.dataset.prev || "Grok2API 导入"; }
    }
  });

document.getElementById("btn-probe-cpa-pool")?.addEventListener("click", async () => {
    const btn = document.getElementById("btn-probe-cpa-pool");
    if (btn) { btn.disabled = true; btn.dataset.prev = btn.textContent; btn.textContent = "测活中..."; }
    try {
      const ok = confirm(
        "开始测活全部 CPA 文件？\n\n" +
        "• 可用 / 软失败：保留\n" +
        "• 硬失败(401/403)：若设置里「不可用自动删除」为开，则删除账号+CPA\n" +
        "• 可随时点顶部「停止」\n\n" +
        "进度看实时日志。是否开始？"
      );
      if (!ok) return;
      toast("[ui] 启动 CPA 池测活 ...");
      const res = await startProbePool({ limit: 0, workers: 4, delete: true });
      toast((__S.probe_started || "CPA 池测活已启动") + " pid=" + ((res.job && res.job.pid) || "?"));
      refreshStatus();
      setTimeout(() => { refreshCpa().catch(() => {}); refreshAccounts().catch(() => {}); }, 4000);
    } catch (err) {
      toast((__S.probe_fail || "测活启动失败") + ": " + (err.message || err), "err");
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = btn.dataset.prev || "测活 CPA 池"; }
    }
  });

document.getElementById("form-backfill").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = {
      limit: Number(fd.get("limit") || 0),
      email: String(fd.get("email") || ""),
      timeout: Number(fd.get("timeout") || 300),
      probe: fd.get("probe") === "on",
      headless: fd.get("headless") === "on",
    };
    try {
      await ensureCpaExportEnabled();
      toast("[ui] starting backfill ...");
      const res = await startBackfill(body);
      toast(__S.bf_started + " pid=" + ((res.job && res.job.pid) || (res.job_result && res.job_result.job && res.job_result.job.pid) || "?"));
      refreshStatus();
      refreshCpa();
    } catch (err) { toast(String(err.message || err), "err"); }
  });

  document.getElementById("btn-gen-cpa-missing")?.addEventListener("click", async () => {
    const btn = document.getElementById("btn-gen-cpa-missing");
    if (btn) { btn.disabled = true; btn.dataset.prev = btn.textContent; btn.textContent = "生成中..."; }
    try {
      await ensureCpaExportEnabled();
      // limit=0 means all accounts missing xai-*.json
      toast("[ui] 为所有缺失 CPA 的账号开始生成 ...");
      const res = await startBackfill({ limit: 0, email: "", timeout: 300, probe: true, headless: false });
      toast((__S.gen_cpa_started || "已开始生成 CPA") + " pid=" + ((res.job && res.job.pid) || (res.job_result && res.job_result.job && res.job_result.job.pid) || "?"));
      await refreshStatus();
      setTimeout(() => { refreshCpa().catch(() => {}); refreshAccounts().catch(() => {}); }, 3000);
    } catch (err) {
      toast("生成 CPA 失败: " + (err.message || err), "err");
      alert("生成 CPA 失败: " + (err.message || err));
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = btn.dataset.prev || "为缺失账号生成 CPA"; }
    }
  });

  document.getElementById("btn-gen-cpa-one")?.addEventListener("click", async () => {
    const btn = document.getElementById("btn-gen-cpa-one");
    if (btn) { btn.disabled = true; }
    try {
      await ensureCpaExportEnabled();
      // pick first account email from table if possible
      let email = "";
      try {
        const data = await api("/api/accounts?limit=500&offset=0");
        const cpa = await api("/api/cpa?limit=500&offset=0");
        const have = new Set((cpa.items || []).map((x) => String(x.email || "").toLowerCase()));
        const miss = (data.items || []).find((a) => a.email && !have.has(String(a.email).toLowerCase()));
        email = miss ? miss.email : ((data.items || [])[0] || {}).email || "";
      } catch (_) {}
      toast("[ui] 生成 CPA: " + (email || "(limit=1)"));
      const res = await startBackfill({ limit: 1, email, timeout: 300, probe: true, headless: false });
      toast((__S.gen_cpa_one || "已开始生成 CPA") + " " + (email || "") + " pid=" + ((res.job && res.job.pid) || "?"));
      await refreshStatus();
      setTimeout(() => { refreshCpa().catch(() => {}); }, 3000);
    } catch (err) {
      toast("生成 CPA 失败: " + (err.message || err), "err");
    } finally {
      if (btn) btn.disabled = false;
    }
  });

    document.getElementById("btn-archive-accounts")?.addEventListener("click", async () => {
    const btn = document.getElementById("btn-archive-accounts");
    const ok = confirm(
      "确认归档账号？\n\n1) 先为缺失账号补生成 CPA（可能较久）\n2) 将 accounts_cli.txt 按日期备份到 accounts_cli_backup/\n3) 清空当前账号列表\n\n请确保当前没有注册/补生成任务在运行。"
    );
    if (!ok) return;
    if (btn) { btn.disabled = true; btn.dataset.prev = btn.textContent; btn.textContent = "归档中..."; }
    try {
      toast("[ui] 开始归档账号（含补生成 CPA）...");
      const res = await api("/api/archive/accounts", {
        method: "POST",
        body: JSON.stringify({ run_backfill: true, backfill_timeout: 3600 }),
        timeoutMs: 3700000,
      });
      let msg = res.archived
        ? ("账号已归档: " + (res.backup_file || "") + "（原 " + (res.account_count || 0) + " 条）")
        : (res.message || "账号归档完成");
      if (res.backfill_warning) msg += "；注意: " + res.backfill_warning;
      toast(msg, res.backfill_warning ? "err" : "ok");
      await Promise.all([refreshAccounts().catch(() => {}), refreshCpa().catch(() => {}), refreshStatus().catch(() => {})]);
    } catch (err) {
      toast("账号归档失败: " + (err.message || err), "err");
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = btn.dataset.prev || "归档账号"; }
    }
  });

  document.getElementById("btn-archive-cpa")?.addEventListener("click", async () => {
    const btn = document.getElementById("btn-archive-cpa");
    const ok = confirm(
      "确认归档 CPA 文件？\n\n会把当前 cpa_auths 下所有 xai-*.json 移动到 cpa_file_backup/cpa_日期/。\n请确保当前没有注册/补生成任务在运行。"
    );
    if (!ok) return;
    if (btn) { btn.disabled = true; btn.dataset.prev = btn.textContent; btn.textContent = "归档中..."; }
    try {
      toast("[ui] 开始归档 CPA 文件...");
      const res = await api("/api/archive/cpa", {
        method: "POST",
        body: JSON.stringify({}),
        timeoutMs: 120000,
      });
      toast(res.message || ("CPA 已归档 " + (res.moved_count || 0) + " 个"), "ok");
      await Promise.all([refreshCpa().catch(() => {}), refreshStatus().catch(() => {})]);
    } catch (err) {
      toast("CPA 归档失败: " + (err.message || err), "err");
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = btn.dataset.prev || "归档 CPA"; }
    }
  });

  document.getElementById("btn-cleanup-cpa-logs")?.addEventListener("click", async () => {
    const btn = document.getElementById("btn-cleanup-cpa-logs");
    let preview = null;
    try {
      preview = await api("/api/cpa/logs");
    } catch (e) {
      toast("读取日志列表失败: " + (e.message || e), "err");
      return;
    }
    const items = (preview && preview.items) || [];
    if (!items.length) {
      toast("没有可清理的日志文件", "ok");
      return;
    }
    const names = items.map((x) => x.file + " (" + (x.size || 0) + "B)").join("\n");
    const ok = confirm(
      "确认清理 cpa_auths 运行时日志？\n\n将删除：\n" +
      names +
      "\n\n默认保留 .upload_state.json（上传状态账本）。\n不会删除任何 xai-*.json 凭证文件。"
    );
    if (!ok) return;
    const hasState = items.some((x) => x.file === ".upload_state.json");
    let includeUploadState = false;
    if (hasState) {
      includeUploadState = confirm(
        "是否同时删除 .upload_state.json（上传状态账本）？\n\n" +
        "点「确定」= 一起删除（后台会显示为未上传）\n" +
        "点「取消」= 仅清理日志，保留上传状态"
      );
    }
    if (btn) {
      btn.disabled = true;
      btn.dataset.prev = btn.textContent;
      btn.textContent = "清理中...";
    }
    try {
      toast("[ui] 正在清理 CPA 日志...");
      const res = await api("/api/cpa/cleanup-logs", {
        method: "POST",
        body: JSON.stringify({ include_upload_state: !!includeUploadState }),
        timeoutMs: 60000,
      });
      toast(res.message || ("已清理 " + (res.deleted_count || 0) + " 个文件"), "ok");
      await Promise.all([refreshCpa().catch(() => {}), refreshStatus().catch(() => {})]);
    } catch (err) {
      toast("清理日志失败: " + (err.message || err), "err");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = btn.dataset.prev || "清理日志";
      }
    }
  });


  function bindAccountsSecrets() {
    const box = document.getElementById("accounts-table");
    if (!box || box.dataset.secretBound === "1") return;
    box.dataset.secretBound = "1";
    box.addEventListener("click", async (ev) => {
      const t = ev.target;
      if (!(t instanceof Element)) return;

      const del = t.closest(".btn-account-del");
      if (del && box.contains(del)) {
        const email = del.getAttribute("data-email") || "";
        if (!email) return;
        const hasCpa = del.getAttribute("data-has-cpa") === "1";
        let msg = "确认从 accounts_cli.txt 删除该账号？\n\n" + email;
        if (hasCpa) msg += "\n\n该账号本地已有 CPA 文件。";
        if (!confirm(msg)) return;
        let deleteCpa = false;
        if (hasCpa) {
          deleteCpa = confirm(
            "是否同时删除对应的 CPA 文件（xai-邮箱.json）？\n\n" +
            "确定 = 账号 + CPA 都删\n取消 = 只删账号行，保留 CPA 文件"
          );
        }
        del.disabled = true;
        try {
          const res = await api("/api/accounts/delete", {
            method: "POST",
            body: JSON.stringify({ email: email, delete_cpa: !!deleteCpa }),
            timeoutMs: 60000,
          });
          toast(res.message || ("已删除 " + email), "ok");
          await Promise.all([
            refreshAccounts().catch(() => {}),
            refreshCpa().catch(() => {}),
            refreshStatus().catch(() => {}),
          ]);
        } catch (err) {
          toast("删除失败: " + (err.message || err), "err");
          del.disabled = false;
        }
        return;
      }

      const btn = t.closest(".btn-eye");
      if (!btn || !box.contains(btn)) return;
      const cell = btn.closest(".secret-cell");
      const text = cell && cell.querySelector(".secret-text");
      if (!text) return;
      const secret = text.getAttribute("data-secret") || "";
      const shown = text.getAttribute("data-shown") === "1";
      if (shown) {
        const masked = secret ? "•".repeat(Math.min(Math.max(secret.length, 8), 24)) : "-";
        text.textContent = masked;
        text.setAttribute("data-shown", "0");
        btn.classList.remove("is-on");
        btn.setAttribute("title", "显示密码");
      } else {
        text.textContent = secret;
        text.setAttribute("data-shown", "1");
        btn.classList.add("is-on");
        btn.setAttribute("title", "隐藏密码");
      }
    });
  }
  bindAccountsSecrets();

// Floating back-to-top
  (function bindBackTop() {
    const btn = document.getElementById("btn-back-top");
    if (!btn) return;
    const toggle = () => {
      const y = window.scrollY || document.documentElement.scrollTop || 0;
      if (y > 280) btn.removeAttribute("hidden");
      else btn.setAttribute("hidden", "");
    };
    window.addEventListener("scroll", toggle, { passive: true });
    btn.addEventListener("click", () => {
      try {
        window.scrollTo({ top: 0, behavior: "smooth" });
      } catch (_) {
        window.scrollTo(0, 0);
      }
    });
    toggle();
  })();

  function bindCpaFilters() {
    const statusEl = document.getElementById("cpa-filter-status");
    const qEl = document.getElementById("cpa-filter-q");
    let timer = null;
    const run = () => {
      listState.cpa.page = 1;
      refreshCpa().catch((e) => toast(String(e.message || e)));
    };
    statusEl?.addEventListener("change", run);
    qEl?.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(run, 280);
    });
    qEl?.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        clearTimeout(timer);
        run();
      }
    });
  }
  
  function bindAccountsFilters() {
    const ssoEl = document.getElementById("accounts-filter-sso");
    const cpaEl = document.getElementById("accounts-filter-cpa");
    const qEl = document.getElementById("accounts-filter-q");
    let timer = null;
    const run = () => {
      listState.accounts.page = 1;
      refreshAccounts().catch((e) => toast(String(e.message || e)));
    };
    ssoEl?.addEventListener("change", run);
    cpaEl?.addEventListener("change", run);
    qEl?.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(run, 280);
    });
    qEl?.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        clearTimeout(timer);
        run();
      }
    });
  }
  bindAccountsFilters();

  bindCpaFilters();

  document.getElementById("btn-refresh-cpa")?.addEventListener("click", () => {
    refreshCpa().catch((e) => toast(String(e.message || e)));
  });

  async function uploadCpaFiles(payload, btn) {
    if (btn) btn.disabled = true;
    try {
      const body = Object.assign({ workers: 4 }, payload || {});
      // bulk pending/all: batch to avoid gateway/proxy timeouts on large sets
      const isBulk = !body.file && !(body.files && body.files.length);
      if (isBulk && body.limit == null) body.limit = 80;
      const data = await api("/api/cpa/upload", {
        method: "POST",
        body: JSON.stringify(body),
        timeoutMs: 300000,
      });
      const ok = data.success || 0;
      const fail = data.failed || 0;
      const total = data.total || 0;
      let msg;
      if (fail === 0) {
        msg = "CPAMC 上传完成：" + ok + "/" + total;
        if (data.truncated) msg += "（本批上限 " + body.limit + "，可再次点击继续）";
        toast(msg, "ok");
      } else {
        const firstErr = (data.results || []).find((r) => r && r.ok === false);
        let detail = firstErr ? (firstErr.error || firstErr.file || "") : "";
        if (detail && typeof detail === "object") detail = JSON.stringify(detail);
        toast("CPAMC 上传：成功 " + ok + "，失败 " + fail + (detail ? " · " + detail : ""), "err");
      }
      await refreshCpa();
      return data;
    } catch (e) {
      let msg = e && e.message ? e.message : String(e);
      toast("CPAMC 上传失败: " + msg, "err");
      throw e;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  document.getElementById("btn-cpa-upload-pending")?.addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    if (!confirm("将所有「未上传」的 CPA 文件上传到 CPAMC？")) return;
    await uploadCpaFiles({ pending_only: true, force: true }, btn).catch(() => {});
  });

  document.getElementById("btn-cpa-upload-all")?.addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    if (!confirm("强制重新上传目录中的全部 CPA 文件到 CPAMC？")) return;
    await uploadCpaFiles({ pending_only: false, force: true }, btn).catch(() => {});
  });

  document.getElementById("cpa-table")?.addEventListener("click", async (ev) => {
    const t = ev.target;
    if (!(t instanceof HTMLElement)) return;
    const upBtn = t.closest(".btn-cpa-upload-one");
    if (upBtn) {
      const file = upBtn.getAttribute("data-file") || "";
      if (!file) return;
      const already = upBtn.getAttribute("data-uploaded") === "1";
      if (already && !confirm("重新上传 " + file + " 到 CPAMC？")) return;
      await uploadCpaFiles({ file: file, force: true }, upBtn).catch(() => {});
      return;
    }
    const markBtn = t.closest(".btn-cpa-mark-one");
    if (markBtn) {
      const file = markBtn.getAttribute("data-file") || "";
      if (!file) return;
      if (!confirm("将 " + file + " 标记为已上传（不实际请求 CPAMC）？")) return;
      markBtn.disabled = true;
      try {
        await api("/api/cpa/mark", {
          method: "POST",
          body: JSON.stringify({ file: file, uploaded: true }),
        });
        toast("已标记为已上传", "ok");
        await refreshCpa();
      } catch (e) {
        toast("标记失败: " + (e.message || e), "err");
      } finally {
        markBtn.disabled = false;
      }
    }
  });


  function bindPager(kind, refreshFn) {
    const prev = document.getElementById(kind + "-prev");
    const next = document.getElementById(kind + "-next");
    const sel = document.getElementById(kind + "-page-size");
    if (sel) {
      sel.value = String(listState[kind].pageSize);
      sel.addEventListener("change", () => {
        const n = parseInt(sel.value, 10);
        listState[kind].pageSize = PAGE_SIZES.includes(n) ? n : 10;
        listState[kind].page = 1;
        savePageSize(kind, listState[kind].pageSize);
        refreshFn().catch((e) => toast(String(e.message || e)));
      });
    }
    if (prev) prev.addEventListener("click", () => {
      if (listState[kind].page > 1) {
        listState[kind].page -= 1;
        refreshFn().catch((e) => toast(String(e.message || e)));
      }
    });
    if (next) next.addEventListener("click", () => {
      if (listState[kind].page < listState[kind].pages) {
        listState[kind].page += 1;
        refreshFn().catch((e) => toast(String(e.message || e)));
      }
    });
  }
  bindPager("accounts", refreshAccounts);
  bindPager("cpa", refreshCpa);

  document.getElementById("btn-stop").addEventListener("click", async () => {
    try {
      await stopJob();
      toast(__S.stop_req);
    } catch (err) { toast(String(err.message || err)); }
  });

  document.getElementById("btn-refresh").addEventListener("click", async () => {
    try {
      await Promise.all([refreshStatus(), refreshAccounts(), refreshCpa(), refreshSettings(), refreshConfig()]);
    } catch (err) { toast(String(err.message || err)); }
  });

  document.getElementById("btn-copy-log")?.addEventListener("click", async () => {
    const el = document.getElementById("log");
    const text = el ? String(el.textContent || "") : "";
    if (!text.trim()) { toast("日志为空"); return; }
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      toast("日志已复制");
    } catch (err) {
      toast("复制失败: " + (err.message || err));
    }
  });

  document.getElementById("btn-clear-log").addEventListener("click", () => {
    document.getElementById("log").textContent = "";
  });

  document.getElementById("btn-save-config").addEventListener("click", async (e) => {
    if (e) { e.preventDefault(); e.stopPropagation(); }
    try {
      const config = JSON.parse(document.getElementById("config-editor").value);
      await api("/api/config", { method: "PUT", body: JSON.stringify({ config }) });
      toastSave(__S.cfg_saved, true);
      refreshStatus();
      refreshConfig();
    } catch (err) { toastSave(__S.save_fail + ": " + (err.message || err), false); }
  });

  document.getElementById("btn-save-cpa")?.addEventListener("click", async () => {
    try {
      const patch = collectQuickConfig();
      // only CPA-related keys
      const cpaPatch = {
        cpa_export_enabled: patch.cpa_export_enabled,
        cpa_headless: patch.cpa_headless,
        cpa_base_url: patch.cpa_base_url,
        cpa_proxy: patch.cpa_proxy,
        cpa_auth_dir: patch.cpa_auth_dir,
        cpa_management_upload_enabled: patch.cpa_management_upload_enabled,
        cpa_management_base: patch.cpa_management_base,
        cpa_probe_usability: patch.cpa_probe_usability,
        cpa_delete_unusable: patch.cpa_delete_unusable,
        local_turnstile_enabled: patch.local_turnstile_enabled,
        local_turnstile_url: patch.local_turnstile_url,
      };
      if (patch.cpa_management_key) cpaPatch.cpa_management_key = patch.cpa_management_key;
      await api("/api/config", { method: "PUT", body: JSON.stringify({ config: cpaPatch }) });
      toastSave(__S.cpa_saved, true);
      await refreshConfig();
      await refreshStatus();
    } catch (err) { toastSave("CPA " + __S.save_fail + ": " + (err.message || err), false); }
  });

  document.getElementById("q-email_provider")?.addEventListener("change", () => {
    syncMailProviderFields();
  });
  syncMailProviderFields();

  document.getElementById("btn-save-quick").addEventListener("click", async () => {
    try {
      const patch = collectQuickConfig();
      await api("/api/config", { method: "PUT", body: JSON.stringify({ config: patch }) });
      toastSave(__S.quick_saved, true);
      await refreshConfig();
      await refreshStatus();
    } catch (err) { toastSave(__S.quick_fail + ": " + (err.message || err), false); }
  });

  document.getElementById("form-settings").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      web_token: document.getElementById("set-token").value.trim() || null,
      novnc_public_url: document.getElementById("set-novnc-url").value.trim(),
      novnc_host: document.getElementById("set-novnc-host").value.trim(),
      novnc_port: document.getElementById("set-novnc-port").value.trim(),
    };
    try {
      const res = await api("/api/settings", { method: "PUT", body: JSON.stringify(body) });
      if (body.web_token) setToken(body.web_token);
      document.getElementById("set-token").value = "";
      toastSave(res.token_changed ? __S.set_saved_token : __S.set_saved, true);
      await refreshSettings();
      applyNovncUrl(res.settings.novnc_url);
    } catch (err) { toastSave(__S.set_fail + ": " + (err.message || err), false); }
  });

  document.getElementById("btn-logout").addEventListener("click", async () => {
    try { await api("/api/logout", { method: "POST", body: "{}" }); } catch (_) {}
    setToken("");
    location.href = "/login";
  });

  async function bootstrap() {
    if (window.__NOVNC_URL__) applyNovncUrl(window.__NOVNC_URL__);
    try {
      await refreshStatus();
      await refreshAccounts();
      await refreshCpa();
      await refreshConfig();
      await refreshSettings();
      const logs = await api("/api/logs?tail=200");
      if (logs.lines) document.getElementById("log").textContent = logs.lines.join("\n");
    } catch (err) { toast(String(err.message || err)); }
  }


  async function refreshBuild() {
    try {
      const h = await api("/api/health", { timeoutMs: 5000 });
      const el = document.getElementById("build-badge");
      if (el && h) el.textContent = "build " + (h.build || h.version || "?");
    } catch (_) {}
  }

  document.getElementById("btn-emergency-start")?.addEventListener("click", async () => {
    const body = {
      extra: Number(document.querySelector('#form-register [name="extra"]')?.value || 1),
      threads: Number(document.querySelector('#form-register [name="threads"]')?.value || 1),
      mint_workers: Number(document.querySelector('#form-register [name="mint_workers"]')?.value || -1),
      fast: !!document.querySelector('#form-register [name="fast"]')?.checked,
    };
    try {
      toast("[ui] emergency start via PUT /api/config _cmd");
      const res = await api("/api/config", {
        method: "PUT",
        timeoutMs: 12000,
        body: JSON.stringify({ config: { _cmd: { action: "start", ...body } } }),
      });
      toast(__S.reg_started + " pid=" + ((res.job && res.job.pid) || (res.job_result && res.job_result.job && res.job_result.job.pid) || "?"));
      await refreshStatus();
    } catch (err) {
      toast("emergency failed: " + (err.message || err), "err");
      alert("emergency failed: " + (err.message || err));
    }
  });


  const PAGE_META = {
    register: { title: "注册任务", sub: "邮箱、代理、任务参数与实时日志" },
    accounts: { title: "账号管理", sub: "本地账号、CPA 文件、归档与补生成" },
    settings: { title: "系统设置", sub: "系统、CPA 对接与完整配置" },
  };
  function switchPage(name, pushHash) {
    const page = PAGE_META[name] ? name : "register";
    document.querySelectorAll(".nav-item").forEach((el) => {
      el.classList.toggle("active", el.dataset.page === page);
    });
    document.querySelectorAll(".page").forEach((el) => {
      const on = el.dataset.page === page;
      el.classList.toggle("active", on);
      if (on) el.removeAttribute("hidden");
      else el.setAttribute("hidden", "");
    });
    const t = document.getElementById("page-title");
    const s = document.getElementById("page-sub");
    if (t) t.textContent = PAGE_META[page].title;
    if (s) s.textContent = PAGE_META[page].sub;
    if (pushHash !== false) {
      try {
        const h = "#/" + page;
        if (location.hash !== h) history.replaceState(null, "", h);
      } catch (_) {}
    }
    if (page === "accounts") {
      refreshAccounts().catch(() => {});
      refreshCpa().catch(() => {});
    } else if (page === "settings") {
      refreshSettings().catch(() => {});
      refreshConfig().catch(() => {});
    } else {
      refreshStatus().catch(() => {});
    }
  }
  function initPageNav() {
    document.querySelectorAll(".nav-item[data-page]").forEach((btn) => {
      btn.addEventListener("click", () => switchPage(btn.dataset.page));
    });
    const raw = (location.hash || "").replace(/^#\/?/, "").trim();
    const page = PAGE_META[raw] ? raw : "register";
    switchPage(page, false);
    window.addEventListener("hashchange", () => {
      const r = (location.hash || "").replace(/^#\/?/, "").trim();
      switchPage(PAGE_META[r] ? r : "register", false);
    });
  }

  initPageNav();
  connectWs();
  refreshBuild();
  bootstrap();
  async function pollLogsWhileRunning() {
    try {
      const st = await api("/api/status");
      if (st.job) setBadge(st.job);
      if (!(st.job && st.job.running)) return;
      const logs = await api("/api/logs?tail=400");
      if (!logs.lines) return;
      const el = document.getElementById("log");
      if (!el) return;
      const text = logs.lines.join("\n");
      if (el.textContent !== text) {
        el.textContent = text;
        el.scrollTop = el.scrollHeight;
      }
    } catch (_) {}
  }
  setInterval(() => {
    refreshStatus().catch(() => {});
    refreshAccounts().catch(() => {});
    refreshCpa().catch(() => {});
    pollLogsWhileRunning().catch(() => {});
  }, 4000);
})();
