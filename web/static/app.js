(() => {
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
      credentials: "same-origin",
    });
    if (res.status === 401) {
      location.href = "/login";
      throw new Error("unauthorized");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.message || res.statusText);
    return data;
  }

  function toast(msg) {
    const log = document.getElementById("log");
    if (log) {
      log.textContent += (log.textContent ? "\n" : "") + `[ui] ${msg}`;
      log.scrollTop = log.scrollHeight;
    }
  }

  function setBadge(job) {
    const el = document.getElementById("run-badge");
    if (!el) return;
    if (job && job.running) {
      el.className = "badge run";
      el.textContent = `??? ? ${job.kind || "job"}`;
    } else if (job && job.exit_code != null && job.exit_code !== 0) {
      el.className = "badge fail";
      el.textContent = `?? code=${job.exit_code}`;
    } else {
      el.className = "badge idle";
      el.textContent = "??";
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
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function applyNovncUrl(url) {
    const a = document.getElementById("link-novnc");
    const prev = document.getElementById("novnc-preview");
    if (a && url) a.href = url;
    if (prev && url) {
      prev.href = url;
      prev.textContent = url;
    }
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
        box.innerHTML = "<strong>??????</strong> " + st.setup_hints.map(esc).join(" ? ");
      } else {
        box.hidden = true;
      }
    }
    return st;
  }

  async function refreshAccounts() {
    const data = await api("/api/accounts?limit=50");
    const box = document.getElementById("accounts-table");
    if (!data.items || !data.items.length) {
      box.innerHTML = "<p class='hint'>????</p>";
      return;
    }
    const rows = data.items
      .map((r) => `<tr><td>${esc(r.email)}</td><td>${esc(r.password)}</td><td>${r.has_sso ? "?" : "-"}</td></tr>`)
      .join("");
    box.innerHTML = `<table><thead><tr><th>??</th><th>??</th><th>SSO</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  async function refreshCpa() {
    const data = await api("/api/cpa?limit=50");
    const box = document.getElementById("cpa-table");
    if (!data.items || !data.items.length) {
      box.innerHTML = "<p class='hint'>?? xai-*.json</p>";
      return;
    }
    const rows = data.items
      .map((r) => `<tr><td>${esc(r.email)}</td><td>${esc(r.mtime)}</td><td>${r.size}</td></tr>`)
      .join("");
    box.innerHTML = `<table><thead><tr><th>??</th><th>mtime</th><th>size</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  async function refreshConfig() {
    const data = await api("/api/config?redact=true");
    document.getElementById("config-editor").value = JSON.stringify(data.config || {}, null, 2);
  }

  async function refreshSettings() {
    const s = await api("/api/settings");
    document.getElementById("token-hint").textContent = s.web_token_hint || "-";
    document.getElementById("set-novnc-url").value = s.novnc_public_url || "";
    document.getElementById("set-novnc-host").value = s.novnc_host || "";
    document.getElementById("set-novnc-port").value = s.novnc_port || "";
    if (s.novnc_url) applyNovncUrl(s.novnc_url);
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
    ws.onclose = () => setTimeout(connectWs, 2500);
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
      toast("???????");
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
      toast("Backfill ???");
      refreshStatus();
    } catch (err) {
      toast(String(err.message || err));
    }
  });

  document.getElementById("btn-stop").addEventListener("click", async () => {
    try {
      await api("/api/jobs/stop", { method: "POST", body: "{}" });
      toast("?????");
    } catch (err) {
      toast(String(err.message || err));
    }
  });

  document.getElementById("btn-refresh").addEventListener("click", async () => {
    try {
      await Promise.all([refreshStatus(), refreshAccounts(), refreshCpa(), refreshSettings()]);
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
      toast("?????");
      refreshStatus();
    } catch (err) {
      toast("????: " + (err.message || err));
    }
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
      toast(res.token_changed ? "??????Token ????" : "?????");
      await refreshSettings();
      applyNovncUrl(res.settings.novnc_url);
    } catch (err) {
      toast("??????: " + (err.message || err));
    }
  });

  document.getElementById("btn-logout").addEventListener("click", async () => {
    try {
      await api("/api/logout", { method: "POST", body: "{}" });
    } catch (_) {}
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
