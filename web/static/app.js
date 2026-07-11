(() => {
  const needToken = window.__NEED_TOKEN__ === true || window.__NEED_TOKEN__ === "true";
  const novncPort = window.__NOVNC_PORT__ || "6080";

  function getToken() {
    return localStorage.getItem("web_token") || "";
  }

  function setToken(t) {
    localStorage.setItem("web_token", t || "");
  }

  function authHeaders(json = true) {
    const h = {};
    if (json) h["Content-Type"] = "application/json";
    const t = getToken();
    if (t) {
      h["Authorization"] = `Bearer ${t}`;
      h["X-Web-Token"] = t;
    }
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
    const res = await fetch(path, {
      ...opts,
      headers: { ...authHeaders(!(opts.body instanceof FormData)), ...(opts.headers || {}) },
    });
    if (res.status === 401) {
      toast("需要 WEB_TOKEN，请点击右上角设置");
      throw new Error("unauthorized");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.message || res.statusText);
    return data;
  }

  function toast(msg) {
    console.log(msg);
    const log = document.getElementById("log");
    if (log) {
      log.textContent += `\n[ui] ${msg}\n`;
      log.scrollTop = log.scrollHeight;
    }
  }

  function setBadge(job) {
    const el = document.getElementById("run-badge");
    if (!el) return;
    if (job && job.running) {
      el.className = "badge run";
      el.textContent = `运行中 · ${job.kind || "job"}`;
    } else if (job && job.exit_code != null && job.exit_code !== 0) {
      el.className = "badge fail";
      el.textContent = `结束 code=${job.exit_code}`;
    } else {
      el.className = "badge idle";
      el.textContent = "空闲";
    }
  }

  function appendLines(lines) {
    if (!lines || !lines.length) return;
    const log = document.getElementById("log");
    log.textContent += (log.textContent ? "\n" : "") + lines.join("\n");
    log.scrollTop = log.scrollHeight;
  }

  async function refreshStatus() {
    const st = await api("/api/status");
    document.getElementById("st-accounts").textContent = st.accounts_count;
    document.getElementById("st-cpa").textContent = st.cpa_count;
    document.getElementById("st-mail").textContent = st.email_provider || "-";
    document.getElementById("st-display").textContent = st.display || "-";
    setBadge(st.job);
    // noVNC on same host, different port
    const host = window.location.hostname;
    const novnc = `http://${host}:${novncPort}/vnc.html?autoconnect=1&resize=scale`;
    document.getElementById("link-novnc").href = novnc;
    document.getElementById("link-accounts").href = withToken("/api/download/accounts");
    return st;
  }

  async function refreshAccounts() {
    const data = await api("/api/accounts?limit=50");
    const box = document.getElementById("accounts-table");
    if (!data.items || !data.items.length) {
      box.innerHTML = "<p class='hint'>暂无账号</p>";
      return;
    }
    const rows = data.items
      .map(
        (r) => `<tr><td>${esc(r.email)}</td><td>${esc(r.password)}</td><td>${r.has_sso ? "✓" : "-"}</td></tr>`
      )
      .join("");
    box.innerHTML = `<table><thead><tr><th>邮箱</th><th>密码</th><th>SSO</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  async function refreshCpa() {
    const data = await api("/api/cpa?limit=50");
    const box = document.getElementById("cpa-table");
    if (!data.items || !data.items.length) {
      box.innerHTML = "<p class='hint'>暂无 xai-*.json</p>";
      return;
    }
    const rows = data.items
      .map((r) => `<tr><td>${esc(r.email)}</td><td>${esc(r.mtime)}</td><td>${r.size}</td></tr>`)
      .join("");
    box.innerHTML = `<table><thead><tr><th>邮箱</th><th>mtime</th><th>size</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  async function refreshConfig() {
    const data = await api("/api/config?redact=true");
    document.getElementById("config-editor").value = JSON.stringify(data.config || {}, null, 2);
  }

  function esc(s) {
    return String(s ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function connectWs() {
    const t = getToken();
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const q = t ? `?token=${encodeURIComponent(t)}` : "";
    const ws = new WebSocket(`${proto}://${location.host}/ws/logs${q}`);
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        appendLines(msg.lines || []);
        if (msg.status) setBadge(msg.status);
      } catch (_) {}
    };
    ws.onclose = () => setTimeout(connectWs, 2000);
  }

  document.getElementById("form-register").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = {
      extra: Number(fd.get("extra") || 1),
      threads: Number(fd.get("threads") || 1),
      mint_workers: Number(fd.get("mint_workers") || -1),
      fast: fd.get("fast") === "on",
    };
    try {
      await api("/api/jobs/register", { method: "POST", body: JSON.stringify(body) });
      toast("注册任务已启动");
      refreshStatus();
    } catch (err) {
      toast(String(err.message || err));
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
      await api("/api/jobs/backfill", { method: "POST", body: JSON.stringify(body) });
      toast("Backfill 已启动");
      refreshStatus();
    } catch (err) {
      toast(String(err.message || err));
    }
  });

  document.getElementById("btn-stop").addEventListener("click", async () => {
    try {
      await api("/api/jobs/stop", { method: "POST", body: "{}" });
      toast("已请求停止");
    } catch (err) {
      toast(String(err.message || err));
    }
  });

  document.getElementById("btn-refresh").addEventListener("click", async () => {
    try {
      await Promise.all([refreshStatus(), refreshAccounts(), refreshCpa()]);
    } catch (err) {
      toast(String(err.message || err));
    }
  });

  document.getElementById("btn-clear-log").addEventListener("click", () => {
    document.getElementById("log").textContent = "";
  });

  document.getElementById("btn-save-config").addEventListener("click", async () => {
    try {
      const config = JSON.parse(document.getElementById("config-editor").value);
      await api("/api/config", { method: "PUT", body: JSON.stringify({ config }) });
      toast("配置已保存");
      refreshStatus();
    } catch (err) {
      toast("保存失败: " + (err.message || err));
    }
  });

  const btnToken = document.getElementById("btn-token");
  if (btnToken) {
    btnToken.addEventListener("click", () => {
      const t = prompt("输入 WEB_TOKEN", getToken() || "");
      if (t != null) setToken(t.trim());
      bootstrap();
    });
  }

  async function bootstrap() {
    if (needToken && !getToken()) {
      toast("此实例启用了 WEB_TOKEN，请先设置 Token");
    }
    try {
      await refreshStatus();
      await refreshAccounts();
      await refreshCpa();
      await refreshConfig();
      const logs = await api("/api/logs?tail=200");
      if (logs.lines) {
        document.getElementById("log").textContent = logs.lines.join("\n");
      }
    } catch (err) {
      toast(String(err.message || err));
    }
  }

  connectWs();
  bootstrap();
  setInterval(() => {
    refreshStatus().catch(() => {});
    refreshAccounts().catch(() => {});
    refreshCpa().catch(() => {});
  }, 8000);
})();
