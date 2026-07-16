"""Optional local Turnstile solver client (YesCaptcha-compatible /turnstile).

Used only as a best-effort fallback for browser mint login when page widget
stalls. Token is session-sensitive; enable only if you run a local solver
(e.g. Camoufox turnstile-solver on 127.0.0.1:5072).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def _join(base: str, path: str) -> str:
    b = (base or "").rstrip("/")
    if not b:
        return ""
    if not path.startswith("/"):
        path = "/" + path
    return b + path


def solver_health(base_url: str, *, timeout: float = 3.0) -> dict[str, Any]:
    """Probe local solver. Returns ok + detail (best-effort)."""
    base = (base_url or "").strip()
    if not base:
        return {"ok": False, "error": "empty base_url"}
    last_err = "unreachable"
    for path in ("/", "/result", "/turnstile"):
        url = _join(base, path)
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                code = getattr(resp, "status", 200)
                return {"ok": True, "status": code, "url": url}
        except urllib.error.HTTPError as e:
            return {"ok": True, "status": e.code, "url": url, "note": "http_error_but_up"}
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            continue
    return {"ok": False, "error": last_err, "base_url": base}


def solve_turnstile(
    website_url: str,
    website_key: str,
    *,
    base_url: str,
    client_key: str = "",
    timeout: float = 120.0,
    poll_interval: float = 2.0,
    proxy: str | None = None,
    action: str = "",
    cdata: str = "",
) -> str:
    """Solve Turnstile via local solver. Returns token or raises RuntimeError."""
    base = (base_url or "").strip()
    if not base:
        raise RuntimeError("local turnstile base_url empty")
    website_url = (website_url or "").strip()
    website_key = (website_key or "").strip()
    if not website_url or not website_key:
        raise RuntimeError("website_url and website_key required")

    try:
        token = _solve_yescaptcha_compat(
            base,
            website_url,
            website_key,
            client_key=client_key,
            timeout=timeout,
            poll_interval=poll_interval,
            proxy=proxy,
            action=action,
            cdata=cdata,
        )
        if token:
            return token
    except Exception:
        pass

    return _solve_get_turnstile(
        base,
        website_url,
        website_key,
        timeout=timeout,
        poll_interval=poll_interval,
        action=action,
        cdata=cdata,
    )


def _http_json(
    method: str,
    url: str,
    *,
    body: dict | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw) if raw else {"errorId": 1, "errorDescription": f"HTTP {e.code}"}
        except Exception:
            raise RuntimeError(f"HTTP {e.code}: {raw[:300]}") from e
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except Exception as e:
        raise RuntimeError(f"invalid json from {url}: {raw[:200]}") from e


def _solve_yescaptcha_compat(
    base: str,
    website_url: str,
    website_key: str,
    *,
    client_key: str,
    timeout: float,
    poll_interval: float,
    proxy: str | None,
    action: str,
    cdata: str,
) -> str:
    task: dict[str, Any] = {
        "type": "TurnstileTaskProxyless",
        "websiteURL": website_url,
        "websiteKey": website_key,
    }
    if action:
        task["pageAction"] = action
    if cdata:
        task["data"] = cdata
    if proxy:
        task["proxy"] = proxy
        task["type"] = "TurnstileTask"
    payload = {"clientKey": client_key or "local", "task": task}
    created = _http_json("POST", _join(base, "/createTask"), body=payload, timeout=20)
    if int(created.get("errorId") or 0) != 0:
        raise RuntimeError(created.get("errorDescription") or str(created)[:200])
    task_id = created.get("taskId") or created.get("task_id")
    if not task_id:
        raise RuntimeError(f"createTask missing taskId: {created}")

    deadline = time.time() + max(10.0, float(timeout))
    while time.time() < deadline:
        res = _http_json(
            "POST",
            _join(base, "/getTaskResult"),
            body={"clientKey": client_key or "local", "taskId": task_id},
            timeout=20,
        )
        status = str(res.get("status") or "").lower()
        if status == "ready":
            sol = res.get("solution") or {}
            token = str(sol.get("token") or sol.get("gRecaptchaResponse") or "").strip()
            if len(token) > 20:
                return token
            raise RuntimeError("ready but empty token")
        if int(res.get("errorId") or 0) != 0 and status not in ("processing", ""):
            raise RuntimeError(res.get("errorDescription") or res.get("errorCode") or "solver error")
        if status in ("failed", "error"):
            raise RuntimeError(res.get("errorDescription") or "solver failed")
        time.sleep(max(0.5, float(poll_interval)))
    raise RuntimeError("local turnstile timeout (createTask path)")


def _solve_get_turnstile(
    base: str,
    website_url: str,
    website_key: str,
    *,
    timeout: float,
    poll_interval: float,
    action: str,
    cdata: str,
) -> str:
    q: dict[str, str] = {"url": website_url, "sitekey": website_key}
    if action:
        q["action"] = action
    if cdata:
        q["cdata"] = cdata
    url = _join(base, "/turnstile") + "?" + urllib.parse.urlencode(q)
    created = _http_json("GET", url, timeout=20)
    if int(created.get("errorId") or 0) != 0 and not created.get("taskId"):
        raise RuntimeError(created.get("errorDescription") or str(created)[:200])
    task_id = created.get("taskId") or created.get("task_id") or created.get("id")
    if not task_id:
        token = str(created.get("token") or (created.get("solution") or {}).get("token") or "").strip()
        if len(token) > 20:
            return token
        raise RuntimeError(f"/turnstile missing taskId: {created}")

    deadline = time.time() + max(10.0, float(timeout))
    while time.time() < deadline:
        res = _http_json(
            "GET",
            _join(base, "/result") + "?" + urllib.parse.urlencode({"id": task_id}),
            timeout=20,
        )
        if isinstance(res, dict):
            val = res.get("value")
            if val and val not in ("CAPTCHA_NOT_READY", "CAPTCHA_FAIL"):
                token = str(val).strip()
                if len(token) > 20:
                    return token
            sol = res.get("solution") or {}
            token = str(sol.get("token") or res.get("token") or "").strip()
            if len(token) > 20:
                return token
            status = str(res.get("status") or "").lower()
            if status == "ready":
                token = str(sol.get("token") or res.get("token") or "").strip()
                if len(token) > 20:
                    return token
            if val == "CAPTCHA_FAIL" or status in ("failed", "error"):
                raise RuntimeError(res.get("errorDescription") or "CAPTCHA_FAIL")
        time.sleep(max(0.5, float(poll_interval)))
    raise RuntimeError("local turnstile timeout (GET path)")


def inject_turnstile_token_js(token: str) -> str:
    """JS snippet to best-effort inject a Turnstile response into the page."""
    safe = (
        (token or "")
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "")
        .replace("\r", "")
    )
    return f"""
(() => {{
  const token = '{safe}';
  if (!token || token.length < 20) return 0;
  let n = 0;
  const setVal = (el) => {{
    try {{
      el.value = token;
      el.setAttribute('value', token);
      el.dispatchEvent(new Event('input', {{ bubbles: true }}));
      el.dispatchEvent(new Event('change', {{ bubbles: true }}));
      n += 1;
    }} catch (e) {{}}
  }};
  document.querySelectorAll('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]').forEach(setVal);
  return n;
}})()
"""


def scrape_sitekey_from_page(page: Any) -> str:
    """Best-effort sitekey scrape from DrissionPage-like object."""
    if page is None:
        return ""
    try:
        js = """
(() => {
  const el = document.querySelector('[data-sitekey]');
  if (el) return String(el.getAttribute('data-sitekey') || '').trim();
  const iframe = document.querySelector('iframe[src*="turnstile"], iframe[src*="challenges.cloudflare"]');
  if (iframe) {
    try {
      const u = new URL(iframe.src);
      const k = u.searchParams.get('sitekey') || u.searchParams.get('k') || '';
      if (k) return String(k).trim();
    } catch (e) {}
  }
  const html = document.documentElement ? document.documentElement.innerHTML : '';
  const m = html.match(/sitekey["'\\s:=]+([0-9A-Za-z_-]{20,})/i);
  return m ? String(m[1] || '').trim() : '';
})()
"""
        v = page.run_js(js)
        return str(v or "").strip()
    except Exception:
        return ""
