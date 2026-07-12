# -*- coding: utf-8 -*-
"""Mint CPA xai auth from SSO cookie via pure HTTP (no browser / no Turnstile).

Adapted from community sso2gropcpa flow:
  1) curl_cffi session with sso cookie
  2) device code request
  3) verify + approve with SSO session
  4) poll tokens
  5) write xai-*.json via schema/writer
"""
from __future__ import annotations

import json
import secrets
import time
from typing import Any, Callable

from .oauth_device import CLIENT_ID, DEVICE_CODE_URL, SCOPE, TOKEN_URL
from .proxyutil import proxy_log_label, resolve_proxy
from .schema import DEFAULT_BASE_URL, build_cpa_xai_auth
from .writer import write_cpa_xai_auth

LogFn = Callable[[str], None]
ISSUER = "https://auth.x.ai"


def _noop(_: str) -> None:
    return None


def _is_rate_limited(url: str = "", body: str = "") -> bool:
    blob = f"{url}\n{body}".lower()
    return any(
        x in blob
        for x in (
            "rate_limited",
            "rate-limited",
            "too_many_requests",
            "ratelimit",
            '"status":429',
        )
    )


def _backoff(base: float, attempt: int, cap: float = 120.0) -> float:
    attempt = max(1, attempt)
    d = min(cap, base * (2 ** min(attempt - 1, 4)))
    return d + secrets.randbelow(5)


def mint_from_sso_http(
    *,
    email: str,
    sso: str,
    auth_dir: str,
    proxy: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    probe: bool = False,
    max_retries: int = 6,
    log: LogFn | None = None,
) -> dict[str, Any]:
    """Use SSO cookie to complete device OAuth over HTTP. No Chromium."""
    log = log or _noop
    email = (email or "").strip()
    sso = (sso or "").strip()
    if not sso:
        return {"ok": False, "email": email, "error": "missing sso cookie"}
    # strip possible "sso=" prefix / quotes
    if sso.lower().startswith("sso="):
        sso = sso.split("=", 1)[1].strip()
    sso = sso.strip().strip('"').strip("'")

    try:
        from curl_cffi import requests as crequests
    except ImportError:
        return {
            "ok": False,
            "email": email,
            "error": "curl_cffi not installed (required for SSO HTTP mint)",
        }

    resolved = resolve_proxy(proxy)
    proxies = {"http": resolved, "https": resolved} if resolved else None
    log(f"sso-http mint start: {email} proxy={proxy_log_label(resolved) or '(none)'}")

    session = crequests.Session()
    # broad domain so accounts.x.ai + auth.x.ai see cookie
    try:
        session.cookies.set("sso", sso, domain=".x.ai")
        session.cookies.set("sso-rw", sso, domain=".x.ai")
    except Exception:
        session.cookies.set("sso", sso)

    try:
        r = session.get(
            "https://accounts.x.ai/",
            impersonate="chrome",
            timeout=20,
            proxies=proxies,
            allow_redirects=True,
        )
    except Exception as e:
        return {"ok": False, "email": email, "error": f"sso probe network: {e}"}

    final_url = str(getattr(r, "url", "") or "")
    if "sign-in" in final_url or "sign-up" in final_url:
        return {"ok": False, "email": email, "error": "sso invalid or expired (redirected to sign-in)"}
    log("sso cookie accepted by accounts.x.ai")

    def request_device() -> dict[str, Any] | None:
        try:
            # prefer curl_cffi for consistency through proxy
            rr = session.post(
                DEVICE_CODE_URL,
                data={"client_id": CLIENT_ID, "scope": SCOPE},
                headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                impersonate="chrome",
                timeout=20,
                proxies=proxies,
            )
            if rr.status_code >= 400:
                log(f"device code HTTP {rr.status_code}: {(rr.text or '')[:200]}")
                return None
            body = rr.json()
            if not isinstance(body, dict):
                return None
            if body.get("user_code") and not body.get("verification_uri_complete"):
                body["verification_uri_complete"] = (
                    f"https://accounts.x.ai/oauth2/device?user_code={body['user_code']}"
                )
            return body
        except Exception as e:
            log(f"device code failed: {e}")
            return None

    def poll_token(device_code: str, interval: int = 5, expires_in: int = 1800) -> dict[str, Any] | None:
        deadline = time.time() + min(float(expires_in or 1800), 180.0)
        while time.time() < deadline:
            try:
                rr = session.post(
                    TOKEN_URL,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": device_code,
                        "client_id": CLIENT_ID,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                    impersonate="chrome",
                    timeout=20,
                    proxies=proxies,
                )
                body = {}
                try:
                    body = rr.json()
                except Exception:
                    body = {"raw": (rr.text or "")[:300]}
                if rr.status_code < 400 and body.get("access_token"):
                    return body
                err = str(body.get("error") or "")
                if err in ("authorization_pending", "slow_down"):
                    time.sleep(max(3, int(interval or 5)))
                    continue
                log(f"token poll: {err or rr.status_code} {(rr.text or '')[:160]}")
                if err:
                    time.sleep(max(3, int(interval or 5)))
                    continue
                return None
            except Exception as e:
                log(f"token poll error: {e}")
                time.sleep(3)
        return None

    dc = request_device()
    if not dc or not dc.get("device_code"):
        return {"ok": False, "email": email, "error": "device code request failed"}
    user_code = dc.get("user_code") or ""
    device_code = dc.get("device_code") or ""
    verification = dc.get("verification_uri_complete") or (
        f"https://accounts.x.ai/oauth2/device?user_code={user_code}"
    )
    log(f"device user_code={user_code} (SSO HTTP path)")

    # warm verification url with SSO session
    try:
        session.get(verification, impersonate="chrome", timeout=20, proxies=proxies, allow_redirects=True)
    except Exception as e:
        log(f"open verification_uri warn: {e}")

    verify_ok = approve_ok = False
    for attempt in range(1, max_retries + 1):
        try:
            r = session.post(
                f"{ISSUER}/oauth2/device/verify",
                data={"user_code": user_code},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                impersonate="chrome",
                timeout=20,
                proxies=proxies,
                allow_redirects=True,
            )
            body_snip = (r.text or "")[:300]
            if _is_rate_limited(str(r.url), body_snip):
                delay = _backoff(12, attempt)
                log(f"verify rate-limited, retry {attempt}/{max_retries} sleep {delay:.0f}s")
                time.sleep(delay)
                dc2 = request_device()
                if not dc2:
                    continue
                user_code = dc2.get("user_code") or user_code
                device_code = dc2.get("device_code") or device_code
                continue
            if "consent" not in str(r.url) and "consent" not in body_snip.lower() and r.status_code >= 400:
                log(f"verify unexpected: url={r.url} status={r.status_code}")
                # still try approve if cookies ok
            verify_ok = True
        except Exception as e:
            delay = _backoff(8, attempt)
            log(f"verify error: {e}; sleep {delay:.0f}s")
            time.sleep(delay)
            continue

        try:
            r = session.post(
                f"{ISSUER}/oauth2/device/approve",
                data={
                    "user_code": user_code,
                    "action": "allow",
                    "principal_type": "User",
                    "principal_id": "",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                impersonate="chrome",
                timeout=20,
                proxies=proxies,
                allow_redirects=True,
            )
            body_snip = (r.text or "")[:300]
            if _is_rate_limited(str(r.url), body_snip):
                delay = _backoff(12, attempt)
                log(f"approve rate-limited, retry {attempt}/{max_retries} sleep {delay:.0f}s")
                time.sleep(delay)
                dc2 = request_device()
                if dc2:
                    user_code = dc2.get("user_code") or user_code
                    device_code = dc2.get("device_code") or device_code
                continue
            # success often redirects to done
            if "done" in str(r.url).lower():
                approve_ok = True
                log(f"approve ok url={r.url}")
                break
            if r.status_code < 400:
                # soft accept: some edges return 200 without done path
                approve_ok = True
                log(f"approve soft-ok url={r.url} status={r.status_code}")
                break
            log(f"approve unexpected: url={r.url} status={r.status_code} body={body_snip[:120]}")
            time.sleep(_backoff(6, attempt))
            continue
        except Exception as e:
            delay = _backoff(8, attempt)
            log(f"approve error: {e}; sleep {delay:.0f}s")
            time.sleep(delay)
            continue

    if not verify_ok and not approve_ok:
        return {"ok": False, "email": email, "error": "device verify/approve failed (SSO HTTP)"}

    tokens = poll_token(device_code, int(dc.get("interval") or 5), int(dc.get("expires_in") or 1800))
    if not tokens or not tokens.get("access_token"):
        return {"ok": False, "email": email, "error": "token poll failed after SSO approve"}

    log(
        f"SSO HTTP tokens ok access_len={len(tokens.get('access_token') or '')} "
        f"refresh={'yes' if tokens.get('refresh_token') else 'no'}"
    )

    payload = build_cpa_xai_auth(
        email=email,
        access_token=tokens["access_token"],
        refresh_token=tokens.get("refresh_token") or "",
        id_token=tokens.get("id_token"),
        expires_in=tokens.get("expires_in"),
        base_url=base_url,
    )
    path = write_cpa_xai_auth(auth_dir, payload)
    result: dict[str, Any] = {
        "ok": True,
        "email": email,
        "path": str(path),
        "method": "sso-http",
        "proxy": proxy_log_label(resolved),
    }

    if probe:
        try:
            from .probe import probe_models

            pr = probe_models(tokens["access_token"], base_url=base_url, proxy=resolved or None)
            result["probe"] = pr
            if not pr.get("ok"):
                result["probe_ok"] = False
                log(f"probe warn: {pr}")
            else:
                result["probe_ok"] = True
                log("probe ok")
        except Exception as e:
            result["probe_error"] = str(e)
            log(f"probe error: {e}")

    log(f"wrote {path}")
    return result
