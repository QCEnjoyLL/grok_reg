"""High-level: mint CPA xai-*.json for one free registered account."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .browser_confirm import mint_with_browser
from .probe import probe_mini_response, probe_models
from .proxyutil import proxy_log_label, resolve_proxy, set_runtime_proxy
from .schema import DEFAULT_BASE_URL, build_cpa_xai_auth
from .sso_http import mint_from_sso_http
from .writer import write_cpa_xai_auth

LogFn = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def _extract_sso(sso: str | None, cookies: Any | None) -> str:
    val = (sso or "").strip()
    if val.lower().startswith("sso="):
        val = val.split("=", 1)[1].strip()
    val = val.strip().strip('"').strip("'")
    if val:
        return val
    if isinstance(cookies, list):
        for c in cookies:
            if not isinstance(c, dict):
                continue
            if c.get("name") in ("sso", "sso-rw") and c.get("value"):
                return str(c.get("value")).strip()
    if isinstance(cookies, dict):
        for k in ("sso", "sso-rw"):
            if cookies.get(k):
                return str(cookies.get(k)).strip()
    return ""


def _access_token_from_cpa_file(path: str | Path) -> str:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    for key in ("access_token", "AccessToken", "accessToken", "key"):
        v = payload.get(key)
        if v:
            return str(v).strip()
    token = payload.get("token")
    if isinstance(token, dict) and token.get("access_token"):
        return str(token["access_token"]).strip()
    oauth = payload.get("oauth")
    if isinstance(oauth, dict) and oauth.get("access_token"):
        return str(oauth["access_token"]).strip()
    return ""


def mint_and_export(
    *,
    email: str,
    password: str = "",
    auth_dir: str | Path,
    page: Any | None = None,
    proxy: str | None = None,
    headless: bool = False,
    base_url: str = DEFAULT_BASE_URL,
    probe: bool = True,
    probe_chat: bool = False,
    browser_timeout_sec: float = 240.0,
    force_standalone: bool = True,
    cookies: Any | None = None,
    sso: str | None = None,
    prefer_sso_http: bool = True,
    reuse_browser: bool = True,
    recycle_every: int = 15,
    log: LogFn | None = None,
    cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Full pipeline: prefer SSO HTTP mint, else browser device-auth -> write CPA.

    Returns dict with keys: ok, path, email, probe, error?
    """
    log = log or _noop
    email = (email or "").strip()
    password = (password or "").strip()
    sso_val = _extract_sso(sso, cookies)

    if not email:
        return {"ok": False, "email": email, "error": "missing email"}
    if not sso_val and not password:
        return {"ok": False, "email": email, "error": "missing password/sso"}

    # Config/explicit proxy wins over shell https_proxy (common 7890 trap).
    # Thread-local pin — safe under concurrent mint workers.
    resolved = resolve_proxy(proxy)
    set_runtime_proxy(resolved or None)
    sso_flag = "yes" if sso_val else "no"
    log(
        "mint start: "
        + email
        + " proxy="
        + (proxy_log_label(resolved) or "(none)")
        + " sso="
        + sso_flag
    )

    # 1) Prefer pure HTTP SSO -> device approve (no Turnstile / no Chromium)
    if prefer_sso_http and sso_val:
        log("try SSO HTTP mint (no browser)")
        try:
            http_result = mint_from_sso_http(
                email=email,
                sso=sso_val,
                auth_dir=str(auth_dir),
                proxy=resolved or None,
                base_url=base_url,
                probe=False,  # unified probe below
                log=log,
            )
        except Exception as e:  # noqa: BLE001
            log("SSO HTTP mint exception: " + str(e))
            http_result = {"ok": False, "email": email, "error": str(e)}

        if http_result.get("ok") and http_result.get("path"):
            result: dict[str, Any] = {
                "ok": True,
                "email": email,
                "path": str(http_result["path"]),
                "method": http_result.get("method") or "sso-http",
                "base_url": base_url,
                "proxy": proxy_log_label(resolved),
            }
            if probe:
                try:
                    access = _access_token_from_cpa_file(result["path"])
                    if access:
                        pr = probe_models(
                            access, base_url=base_url, proxy=resolved or None
                        )
                        result["probe_models"] = pr
                        log(
                            "probe models: ok="
                            + str(pr.get("ok"))
                            + " has_grok_45="
                            + str(pr.get("has_grok_45"))
                            + " ids="
                            + str(pr.get("model_ids"))
                        )
                        if not pr.get("has_grok_45"):
                            result["ok"] = False
                            result["error"] = "token ok but grok-4.5 not listed"
                        if probe_chat and pr.get("has_grok_45"):
                            ch = probe_mini_response(
                                access, base_url=base_url, proxy=resolved or None
                            )
                            result["probe_chat"] = ch
                            log(
                                "probe chat: ok="
                                + str(ch.get("ok"))
                                + " model="
                                + str(ch.get("model"))
                                + " text="
                                + repr(ch.get("text"))
                            )
                            if not ch.get("ok"):
                                result["ok"] = False
                                result["error"] = (
                                    "chat probe failed: "
                                    + str(ch.get("error") or ch.get("status"))
                                )
                    else:
                        log("probe skip: access_token not found in CPA file")
                except Exception as e:  # noqa: BLE001
                    log("probe error: " + str(e))
                    result["probe_error"] = str(e)
            return result

        err = str(http_result.get("error") or http_result)
        log("SSO HTTP mint failed: " + err + "; fallback to browser")
        if not password:
            return {
                "ok": False,
                "email": email,
                "error": "SSO HTTP failed and no password for browser fallback: " + err,
                "sso_http_error": http_result.get("error"),
            }

    elif sso_val and not prefer_sso_http:
        log("SSO present but prefer_sso_http=false; using browser mint")
    elif not sso_val:
        log("no SSO cookie; using browser mint")

    if not password:
        return {"ok": False, "email": email, "error": "missing password for browser mint"}

    try:
        tokens = mint_with_browser(
            email=email,
            password=password,
            page=None if force_standalone else page,
            proxy=resolved or None,
            headless=headless,
            browser_timeout_sec=browser_timeout_sec,
            force_standalone=force_standalone,
            cookies=cookies,
            reuse_browser=reuse_browser,
            recycle_every=recycle_every,
            poll_log=log,
            cancel=cancel,
        )
    except Exception as e:  # noqa: BLE001
        log("mint failed: " + str(e))
        return {"ok": False, "email": email, "error": str(e)}

    payload = build_cpa_xai_auth(
        email=email,
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        id_token=tokens.get("id_token"),
        expires_in=tokens.get("expires_in"),
        base_url=base_url,
    )
    path = write_cpa_xai_auth(auth_dir, payload)
    log("wrote " + str(path))

    result = {
        "ok": True,
        "email": email,
        "path": str(path),
        "method": "browser",
        "user_code": tokens.get("user_code"),
        "base_url": base_url,
        "proxy": proxy_log_label(resolved),
    }

    if probe:
        pr = probe_models(
            tokens["access_token"], base_url=base_url, proxy=resolved or None
        )
        result["probe_models"] = pr
        log(
            "probe models: ok="
            + str(pr.get("ok"))
            + " has_grok_45="
            + str(pr.get("has_grok_45"))
            + " ids="
            + str(pr.get("model_ids"))
        )
        if not pr.get("has_grok_45"):
            result["ok"] = False
            result["error"] = "token ok but grok-4.5 not listed"
        if probe_chat and pr.get("has_grok_45"):
            ch = probe_mini_response(
                tokens["access_token"], base_url=base_url, proxy=resolved or None
            )
            result["probe_chat"] = ch
            log(
                "probe chat: ok="
                + str(ch.get("ok"))
                + " model="
                + str(ch.get("model"))
                + " text="
                + repr(ch.get("text"))
            )
            if not ch.get("ok"):
                result["ok"] = False
                result["error"] = (
                    "chat probe failed: " + str(ch.get("error") or ch.get("status"))
                )
    return result
