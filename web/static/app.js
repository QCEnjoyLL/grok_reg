(() => {
  const __S = window.__S = {"idle": "空闲", "running": "运行中", "ended": "结束", "precheck": "运行前检查：", "no_accounts": "暂无账号", "no_cpa": "暂无 xai-*.json", "email": "邮箱", "password": "密码", "reg_started": "注册任务已启动", "bf_started": "Backfill 已启动", "stop_req": "已请求停止", "cfg_saved": "配置已保存", "save_fail": "保存失败", "set_saved_token": "设置已保存（Token 已更新）", "set_saved": "设置已保存", "set_fail": "设置保存失败", "quick_saved": "必要配置已保存", "quick_fail": "必要配置保存失败", "save_ok_title": "保存成功", "save_fail_title": "保存失败", "ok_title": "成功", "err_title": "失败", "cpa_saved": "CPA 配置已保存"};
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
    if (!res.ok) throw new Error((data.detail || data.message || res.statusText) + " @ " + path);
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
    const t = title || (type === "err" ? (__S.save_fail_title || __S.err_title || "Error") : (__S.save_ok_title || __S.ok_title || "OK"));
    el.innerHTML = '<div class="toast-title"></div><div class="toast-msg"></div>';
    el.querySelector(".toast-title").textContent = t;
    el.querySelector(".toast-msg").textContent = String(msg || "");
    host.appendChild(el);
    setTimeout(() => {
      el.style.opacity = "0";
      el.style.transition = "opacity .2s";
      setTimeout(() => el.remove(), 220);
    }, type === "err" ? 4500 : 2800);
  }
  function toast(msg, type = "ok", title = "") {
    // always popup
    popupToast(msg, type, title);
    // also append to log if present
    const log = document.getElementById("log");
    if (log) {
      log.textContent += (log.textContent ? "\n" : "") + "[ui] " + msg;
      log.scrollTop = log.scrollHeight;
    }
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
  function fillQuickFromConfig(cfg) {
    if (!cfg) return;
    setVal("q-email_provider", cfg.email_provider || "moemail");
    setVal("q-moemail_api_base", cfg.moemail_api_base || "https://mail.nloln.cn");
    setVal("q-moemail_api_key", cfg.moemail_api_key || "");
    setVal("q-defaultDomains", cfg.defaultDomains || "");
    setVal("q-cloudflare_api_base", cfg.cloudflare_api_base || "");
    setVal("q-cloudflare_api_key", cfg.cloudflare_api_key || "");
    setVal("q-cloudflare_auth_mode", cfg.cloudflare_auth_mode || "x-admin-auth");
    setVal("q-cloudflare_path_accounts", cfg.cloudflare_path_accounts || "/admin/new_address");
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
    setVal("q-cloudmail_url", cfg.cloudmail_url || "");
    setVal("q-cloudmail_admin_email", cfg.cloudmail_admin_email || "");
    setVal("q-cloudmail_password", cfg.cloudmail_password || "");
  }
  function collectQuickConfig() {
    const bool = (v) => v === "true" || v === true;
    const out = {
      email_provider: getVal("q-email_provider") || "moemail",
      moemail_api_base: getVal("q-moemail_api_base") || "https://mail.nloln.cn",
      defaultDomains: getVal("q-defaultDomains"),
      cloudflare_api_base: getVal("q-cloudflare_api_base"),
      cloudflare_auth_mode: getVal("q-cloudflare_auth_mode") || "x-admin-auth",
      cloudflare_path_accounts: getVal("q-cloudflare_path_accounts") || "/admin/new_address",
      cloudflare_path_messages: getVal("q-cloudflare_path_messages") || "/api/mails",
      cloudflare_path_domains: getVal("q-cloudflare_path_domains") || "/api/domains",
      cloudflare_path_token: getVal("q-cloudflare_path_token") || "/api/token",
      proxy: getVal("q-proxy"),
      cpa_proxy: getVal("q-cpa_proxy"),
      cpa_export_enabled: bool(getVal("q-cpa_export_enabled") || "true"),
      cpa_headless: bool(getVal("q-cpa_headless") || "false"),
      cpa_base_url: getVal("q-cpa_base_url") || "https://cli-chat-proxy.grok.com/v1",
      cpa_auth_dir: getVal("q-cpa_auth_dir") || "./cpa_auths",
      cpa_management_upload_enabled: bool(getVal("q-cpa_management_upload_enabled") || "false"),
      cpa_management_base: getVal("q-cpa_management_base"),
      cloudmail_url: getVal("q-cloudmail_url"),
      cloudmail_admin_email: getVal("q-cloudmail_admin_email"),
    };
    const key = getVal("q-cloudflare_api_key");
    if (key && !key.includes("*")) out.cloudflare_api_key = key;
    const mkey = getVal("q-moemail_api_key");
    if (mkey && !mkey.includes("*")) out.moemail_api_key = mkey;
    const cpaKey = getVal("q-cpa_management_key");
    if (cpaKey && !cpaKey.includes("*")) out.cpa_management_key = cpaKey;
    const cmp = getVal("q-cloudmail_password");
    if (cmp && !cmp.includes("*")) out.cloudmail_password = cmp;
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
    const box = document.getElementById("setup-hints");
    if (box && st.setup_hints) {
      if (st.setup_hints.length) {
        box.hidden = false;
        box.innerHTML = "<strong>" + esc(__S.precheck) + "</strong> " + st.setup_hints.map(esc).join(" · ");
      } else box.hidden = true;
    }
    return st;
  }
  async function refreshAccounts() {
    const data = await api("/api/accounts?limit=50");
    const box = document.getElementById("accounts-table");
    if (!data.items || !data.items.length) {
      box.innerHTML = "<p class='hint'>" + esc(__S.no_accounts) + "</p>";
      return;
    }
    const rows = data.items.map((r) => `<tr><td>${esc(r.email)}</td><td>${esc(r.password)}</td><td>${r.has_sso ? "?" : "-"}</td></tr>`).join("");
    box.innerHTML = `<table><thead><tr><th>${esc(__S.email)}</th><th>${esc(__S.password)}</th><th>SSO</th></tr></thead><tbody>${rows}</tbody></table>`;
  }
  async function refreshCpa() {
    const data = await api("/api/cpa?limit=50");
    const box = document.getElementById("cpa-table");
    if (!data.items || !data.items.length) {
      box.innerHTML = "<p class='hint'>" + esc(__S.no_cpa) + "</p>";
      return;
    }
    const rows = data.items.map((r) => `<tr><td>${esc(r.email)}</td><td>${esc(r.mtime)}</td><td>${r.size}</td></tr>`).join("");
    box.innerHTML = `<table><thead><tr><th>${esc(__S.email)}</th><th>mtime</th><th>size</th></tr></thead><tbody>${rows}</tbody></table>`;
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
        if (msg.lines && msg.lines.length) appendLines(msg.lines);
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
      toast("[ui] " + name + " ...");
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
          toast("[ui] " + name + " no job field, try next", "err");
          return null;
        }
        if (res.ok || hasJob) return res.job_result || res;
        errors.push(name + ": unexpected response");
        return null;
      } catch (err) {
        const msg = String(err.message || err);
        errors.push(name + ": " + msg);
        toast("[ui] " + name + " failed: " + msg, "err");
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

  document.getElementById("form-backfill").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = {
      limit: Number(fd.get("limit") || 1),
      email: String(fd.get("email") || ""),
      timeout: Number(fd.get("timeout") || 300),
      probe: fd.get("probe") === "on",
      headless: fd.get("headless") === "on",
    };
    try {
      try {
        await api("/api/task", { method: "PUT", body: JSON.stringify({ action: "backfill", ...body }) });
      } catch (_) {
        try { await wsSend("backfill", body); }
        catch (__) { await api("/api/jobs/backfill", { method: "POST", body: JSON.stringify(body) }); }
      }
      toast(__S.bf_started);
      refreshStatus();
    } catch (err) { toast(String(err.message || err)); }
  });

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

  document.getElementById("btn-clear-log").addEventListener("click", () => {
    document.getElementById("log").textContent = "";
  });

  document.getElementById("btn-save-config").addEventListener("click", async (e) => {
    if (e) { e.preventDefault(); e.stopPropagation(); }
    try {
      const config = JSON.parse(document.getElementById("config-editor").value);
      await api("/api/config", { method: "PUT", body: JSON.stringify({ config }) });
      toast(__S.cfg_saved, "ok", __S.save_ok_title);
      refreshStatus();
      refreshConfig();
    } catch (err) { toast(__S.save_fail + ": " + (err.message || err), "err", __S.save_fail_title); }
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
      };
      if (patch.cpa_management_key) cpaPatch.cpa_management_key = patch.cpa_management_key;
      await api("/api/config", { method: "PUT", body: JSON.stringify({ config: cpaPatch }) });
      toast(__S.cpa_saved, "ok", __S.save_ok_title);
      await refreshConfig();
      await refreshStatus();
    } catch (err) { toast("CPA " + __S.save_fail + ": " + (err.message || err), "err", __S.save_fail_title); }
  });

  document.getElementById("btn-save-quick").addEventListener("click", async () => {
    try {
      const patch = collectQuickConfig();
      await api("/api/config", { method: "PUT", body: JSON.stringify({ config: patch }) });
      toast(__S.quick_saved, "ok", __S.save_ok_title);
      await refreshConfig();
      await refreshStatus();
    } catch (err) { toast(__S.quick_fail + ": " + (err.message || err), "err", __S.save_fail_title); }
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
      toast(res.token_changed ? __S.set_saved_token : __S.set_saved, "ok", __S.save_ok_title);
      await refreshSettings();
      applyNovncUrl(res.settings.novnc_url);
    } catch (err) { toast(__S.set_fail + ": " + (err.message || err), "err", __S.save_fail_title); }
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

  connectWs();
  bootstrap();
  setInterval(() => {
    refreshStatus().catch(() => {});
    refreshAccounts().catch(() => {});
    refreshCpa().catch(() => {});
  }, 8000);
})();
