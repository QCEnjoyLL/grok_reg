"""Register-machine hook: mint CPA xai auth after successful registration.

OIDC package lives at ./cpa_xai (bundled with this project).
Optional override: config `api_reverse_tools` / env `API_REVERSE_TOOLS`
points at a directory that *contains* the `cpa_xai` package.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import urllib.request
import uuid
from pathlib import Path
import threading
from typing import Any, Callable

_REG_DIR = Path(__file__).resolve().parent
_DEFAULT_OUT = _REG_DIR / "cpa_auths"
_DEFAULT_CPA = Path("")  # empty = do not assume a machine-local CPA path
_UPLOAD_STATE_LOCK = threading.RLock()


def resolve_cpa_proxy(cfg: dict) -> str:
    """Resolve CPA outbound proxy, supporting an explicit direct mode."""

    def _usable(p: str) -> str:
        s = str(p or "").strip()
        if not s:
            return ""
        if s.lower() in {"direct", "none", "off", "disabled"}:
            return ""
        # reject UI-redacted values that were accidentally persisted
        if "*" in s and s.count("*") >= max(3, len(s) // 3):
            return ""
        return s

    configured = _usable(str(cfg.get("cpa_proxy") or ""))
    if configured:
        return configured
    fallback = _usable(str(cfg.get("proxy") or ""))
    if fallback:
        return fallback
    return (
        os.environ.get("https_proxy")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("http_proxy")
        or ""
    ).strip()




def _accounts_file_from_cfg(cfg: dict) -> Path:
    raw = str(cfg.get("accounts_file") or cfg.get("accounts_cli") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (_REG_DIR / p).resolve()
        return p
    # Docker / local defaults
    data_dir = Path(os.environ.get("DATA_DIR") or (_REG_DIR / "data")).expanduser()
    cand = [
        data_dir / "accounts_cli.txt",
        _REG_DIR / "accounts_cli.txt",
        Path(os.environ.get("GROK_REG_DATA_DIR") or data_dir) / "accounts_cli.txt",
    ]
    for c in cand:
        if c.is_file():
            return c
    return data_dir / "accounts_cli.txt"


def _delete_cpa_files_for_email(email: str, auth_dir: str | Path) -> list[str]:
    d = Path(auth_dir)
    deleted: list[str] = []
    if not d.is_dir():
        return deleted
    em = (email or "").strip()
    if not em:
        return deleted
    # exact + case-insensitive
    candidates = [d / f"xai-{em}.json"]
    for f in d.glob("xai-*.json"):
        name = f.name[len("xai-") : -len(".json")]
        if name.lower() == em.lower() and f not in candidates:
            candidates.append(f)
    state_keys: list[str] = []
    for fp in candidates:
        if fp.is_file():
            try:
                fp.unlink()
                deleted.append(fp.name)
                state_keys.append(fp.name)
            except Exception:
                pass
    if state_keys:
        state_path = d / ".upload_state.json"
        if state_path.is_file():
            try:
                st = json.loads(state_path.read_text(encoding="utf-8"))
                if isinstance(st, dict):
                    for k in state_keys:
                        st.pop(k, None)
                    state_path.write_text(
                        json.dumps(st, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
            except Exception:
                pass
    return deleted


def purge_unusable_cpa_account(
    *,
    email: str,
    cfg: dict,
    auth_dir: str | Path | None = None,
    cpa_path: str | Path | None = None,
    log_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Delete unusable account line + CPA auth file(s)."""
    log = log_callback or (lambda m: None)
    email = (email or "").strip()
    out: dict[str, Any] = {"email": email, "account_removed": 0, "cpa_deleted": []}
    if not email:
        return out

    # CPA files
    dirs: list[Path] = []
    if cpa_path:
        p = Path(cpa_path)
        if p.is_file():
            try:
                p.unlink()
                out["cpa_deleted"].append(p.name)
            except Exception as e:
                out["cpa_delete_error"] = str(e)
            dirs.append(p.parent)
    if auth_dir:
        dirs.append(Path(auth_dir))
    cfg_dir = str(cfg.get("cpa_auth_dir") or "").strip()
    if cfg_dir:
        d = Path(cfg_dir).expanduser()
        if not d.is_absolute():
            d = (_REG_DIR / d).resolve()
        dirs.append(d)
    # unique dirs
    seen: set[str] = set()
    for d in dirs:
        try:
            key = str(d.resolve())
        except Exception:
            key = str(d)
        if key in seen:
            continue
        seen.add(key)
        for name in _delete_cpa_files_for_email(email, d):
            if name not in out["cpa_deleted"]:
                out["cpa_deleted"].append(name)

    # accounts_cli.txt
    try:
        from cpa_xai.accounts import remove_accounts_by_email
        acc_path = _accounts_file_from_cfg(cfg)
        ar = remove_accounts_by_email(acc_path, [email])
        out["account_removed"] = int(ar.get("removed_count") or 0)
        out["accounts_file"] = str(acc_path)
        out["accounts_remaining"] = ar.get("remaining")
    except Exception as e:
        out["account_delete_error"] = str(e)
        log(f"[cpa] purge account line failed: {e}")

    log(
        f"[cpa] purged unusable {email}: accounts={out.get('account_removed')} "
        f"cpa_files={out.get('cpa_deleted')}"
    )
    return out



def maybe_probe_and_purge_unusable(
    result: dict[str, Any],
    *,
    email: str,
    cfg: dict,
    auth_dir: str | Path | None = None,
    log_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """After mint, probe chat usability; hard 401/403 -> delete account+CPA.

    Mutates and returns *result*. Soft failures (network/429/etc.) keep files.
    """
    log = log_callback or (lambda m: None)
    cfg = cfg or {}
    auto_probe = bool(cfg.get("cpa_probe_usability", True))
    auto_delete = bool(cfg.get("cpa_delete_unusable", True))
    if not (result.get("ok") and result.get("path") and auto_probe):
        return result
    try:
        from cpa_xai.probe import probe_usability
        from cpa_xai.mint import access_token_from_cpa_file

        access = ""
        for k in ("access_token", "token"):
            v = result.get(k)
            if isinstance(v, str) and v.strip():
                access = v.strip()
                break
            if isinstance(v, dict) and v.get("access_token"):
                access = str(v.get("access_token")).strip()
                break
        if not access:
            access = access_token_from_cpa_file(result.get("path") or "")
        if not access:
            log("[cpa] usability probe skipped: no access_token in result/file")
            return result

        base_url = str(cfg.get("cpa_base_url") or "https://cli-chat-proxy.grok.com/v1")
        proxy = resolve_cpa_proxy(cfg)
        usability = probe_usability(access, base_url=base_url, proxy=proxy or None)
        result["usability"] = usability
        log(
            f"[cpa] usability: usable={usability.get('usable')} unusable={usability.get('unusable')} "
            f"status={usability.get('status')} reason={usability.get('reason')}"
        )
        if usability.get("unusable") and auto_delete:
            purged = purge_unusable_cpa_account(
                email=email,
                cfg=cfg,
                auth_dir=auth_dir,
                cpa_path=result.get("path"),
                log_callback=log,
            )
            result["ok"] = False
            result["unusable"] = True
            result["purged"] = purged
            result["error"] = f"unusable account deleted: {usability.get('reason')}"
            result["path"] = ""
            log(f"[cpa] ! unusable -> deleted account+CPA: {email}")
    except Exception as e:  # noqa: BLE001
        log(f"[cpa] usability probe failed: {e}")
        result["usability_error"] = str(e)
    return result


def _upload_state_path(auth_dir: str | Path | None = None) -> Path:
    base = Path(auth_dir).expanduser() if auth_dir else _DEFAULT_OUT
    if not base.is_absolute():
        base = (_REG_DIR / base).resolve()
    return base / ".upload_state.json"


def load_cpa_upload_state(auth_dir: str | Path | None = None) -> dict:
    path = _upload_state_path(auth_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def mark_cpa_uploaded(
    auth_path: str | Path,
    *,
    ok: bool = True,
    detail: str = "",
    auth_dir: str | Path | None = None,
) -> None:
    """Persist local CPAMC upload status next to cpa auth files (thread-safe)."""
    src = Path(auth_path)
    state_dir = Path(auth_dir).expanduser() if auth_dir else src.parent
    if not state_dir.is_absolute():
        state_dir = (_REG_DIR / state_dir).resolve()
    state_path = state_dir / ".upload_state.json"
    key = src.name
    from datetime import datetime, timezone

    entry = {
        "uploaded": bool(ok),
        "file": key,
        "at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "detail": str(detail or "")[:300],
    }
    with _UPLOAD_STATE_LOCK:
        state = load_cpa_upload_state(state_dir)
        state[key] = entry
        text = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
            tmp = state_path.with_suffix(state_path.suffix + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(state_path)
        except Exception:
            try:
                state_path.write_text(text, encoding="utf-8")
            except Exception:
                pass



def upload_cpa_auth_file(
    auth_path: str | Path,
    config: dict,
    log_callback: Callable[[str], None] | None = None,
) -> dict:
    """Upload one auth JSON file through the CLIProxyAPI management API."""
    log = log_callback or (lambda m: print(m, flush=True))
    src = Path(auth_path).resolve()
    base = str(config.get("cpa_management_base") or "").strip().rstrip("/")
    key = str(config.get("cpa_management_key") or "").strip()
    if not base or not key:
        raise ValueError("CPA management 地址或密码未配置")
    if base.endswith("/v0/management"):
        base = base[: -len("/v0/management")]

    boundary = f"----grokreg{uuid.uuid4().hex}"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode("ascii"),
            (
                'Content-Disposition: form-data; name="file"; '
                f'filename="{src.name}"\r\n'
            ).encode("utf-8"),
            b"Content-Type: application/json\r\n\r\n",
            src.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode("ascii"),
        ]
    )
    request = urllib.request.Request(
        f"{base}/v0/management/auth-files",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=30) as response:
        raw = response.read().decode("utf-8", errors="replace")
        status = int(getattr(response, "status", 200) or 200)
    if status < 200 or status >= 300:
        raise RuntimeError(f"CPA management upload HTTP {status}: {raw[:300]}")
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {"response": raw[:300]}
    log(f"[cpa] management upload -> {base} ({src.name})")
    mark_cpa_uploaded(src, ok=True, detail=f"status={status}", auth_dir=src.parent)
    return {"ok": True, "status": status, "response": payload}


def _ensure_cpa_xai_on_path(tools_dir: str | Path | None = None) -> Path:
    """Put the parent of `cpa_xai` on sys.path. Default: this project root."""
    if tools_dir:
        tools = Path(tools_dir).expanduser().resolve()
    else:
        env = (os.environ.get("API_REVERSE_TOOLS") or "").strip()
        tools = Path(env).expanduser().resolve() if env else _REG_DIR
    # If user pointed at .../cpa_xai itself, use its parent
    if tools.name == "cpa_xai" and (tools / "__init__.py").is_file():
        tools = tools.parent
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    return tools


def export_cookies_from_page(page: Any) -> list[dict]:
    """Best-effort export of cookies from a DrissionPage tab/browser."""
    if page is None:
        return []
    cookies = None
    for getter in (
        lambda: page.cookies(all_domains=True, all_info=True),
        lambda: page.cookies(all_domains=True),
        lambda: page.cookies(),
    ):
        try:
            cookies = getter()
            if cookies:
                break
        except TypeError:
            continue
        except Exception:
            continue
    if not cookies:
        try:
            browser = getattr(page, "browser", None)
            if browser is not None:
                cookies = browser.cookies()
        except Exception:
            cookies = None
    if isinstance(cookies, list):
        return [c for c in cookies if isinstance(c, dict)]
    return []


def export_cpa_xai_for_account(
    email: str,
    password: str,
    *,
    page: Any | None = None,
    cookies: Any | None = None,
    sso: str | None = None,
    config: dict | None = None,
    log_callback: Callable[[str], None] | None = None,
) -> dict:
    """Mint OIDC + write xai-<email>.json under register cpa_auths (and optional CPA auth-dir)."""
    cfg = config or {}
    log = log_callback or (lambda m: print(m, flush=True))

    if not cfg.get("cpa_export_enabled", True):
        log("[cpa] export disabled")
        return {"ok": False, "skipped": True, "reason": "disabled"}

    tools_dir = cfg.get("api_reverse_tools") or cfg.get("cpa_xai_parent") or None
    _ensure_cpa_xai_on_path(tools_dir)

    try:
        from cpa_xai import mint_and_export  # type: ignore
    except Exception as e:  # noqa: BLE001
        log(f"[cpa] import cpa_xai failed: {e}")
        return {"ok": False, "error": f"import: {e}"}

    out_dir = Path(cfg.get("cpa_auth_dir") or _DEFAULT_OUT).expanduser()
    if not out_dir.is_absolute():
        out_dir = (_REG_DIR / out_dir).resolve()

    hotload_raw = (cfg.get("cpa_hotload_dir") or "").strip()
    cpa_dir = Path(hotload_raw).expanduser() if hotload_raw else None
    if cpa_dir and not cpa_dir.is_absolute():
        cpa_dir = (_REG_DIR / cpa_dir).resolve()

    # Priority: cpa_proxy > proxy > env. "direct" explicitly disables all proxies.
    proxy = resolve_cpa_proxy(cfg)
    # Default headed: headless is frequently Cloudflare-blocked on accounts.x.ai
    headless = bool(cfg.get("cpa_headless", False))
    probe = bool(cfg.get("cpa_probe_after_write", True))
    probe_chat = bool(cfg.get("cpa_probe_chat", False))
    timeout = float(cfg.get("cpa_mint_timeout_sec", 240))
    base_url = cfg.get("cpa_base_url") or "https://cli-chat-proxy.grok.com/v1"
    force_standalone = bool(cfg.get("cpa_force_standalone", True))
    cookie_inject = bool(cfg.get("cpa_mint_cookie_inject", True))
    reuse_browser = bool(cfg.get("cpa_mint_browser_reuse", True))
    recycle_every = int(cfg.get("cpa_mint_browser_recycle_every", 15) or 0)

    # cookies: explicit arg > page export > none
    use_cookies = cookies
    if use_cookies is None and cookie_inject and page is not None:
        use_cookies = export_cookies_from_page(page)

    # Extract SSO early (HTTP mint path needs it even if cookie inject is off)
    sso_val = (sso or "").strip()
    if not sso_val and isinstance(use_cookies, list):
        for c in use_cookies:
            if isinstance(c, dict) and c.get("name") in ("sso", "sso-rw") and c.get("value"):
                sso_val = str(c.get("value"))
                break

    if not cookie_inject:
        use_cookies = None
    else:
        # Always attach SSO cookie clones — register cookies alone often miss accounts.x.ai host
        if sso_val:
            base = list(use_cookies) if isinstance(use_cookies, list) else []
            for name in ("sso", "sso-rw"):
                for dom in (".x.ai", "accounts.x.ai", ".accounts.x.ai", "auth.x.ai", "grok.com", ".grok.com"):
                    base.append({
                        "name": name,
                        "value": sso_val,
                        "domain": dom,
                        "path": "/",
                        "secure": True,
                        "httpOnly": True,
                    })
            use_cookies = base

    out_dir.mkdir(parents=True, exist_ok=True)
    prefer_sso = bool(cfg.get("cpa_prefer_sso_http", True))
    log(
        f"[cpa] mint OIDC for {email} -> {out_dir} proxy={proxy or '(none)'} "
        f"cookies={len(use_cookies) if isinstance(use_cookies, list) else (1 if use_cookies else 0)} "
        f"sso={'yes' if sso_val else 'no'} prefer_sso_http={prefer_sso} reuse={reuse_browser}"
    )

    def _log(msg: str) -> None:
        log(f"[cpa] {msg}")

    sso_for_mint = sso_val or (sso or "")
    result = mint_and_export(
        email=email,
        password=password,
        auth_dir=out_dir,
        page=None if force_standalone else page,
        proxy=proxy or None,
        headless=headless,
        base_url=base_url,
        probe=probe,
        probe_chat=probe_chat,
        browser_timeout_sec=timeout,
        force_standalone=force_standalone,
        cookies=use_cookies,
        sso=sso_for_mint,
        prefer_sso_http=prefer_sso,
        reuse_browser=reuse_browser,
        recycle_every=recycle_every,
        log=_log,
    )

    result = maybe_probe_and_purge_unusable(
        result,
        email=email,
        cfg=cfg,
        auth_dir=out_dir,
        log_callback=log,
    )

    if result.get("ok") and result.get("path") and cfg.get("cpa_copy_to_hotload", False) and cpa_dir:
        try:
            cpa_dir.mkdir(parents=True, exist_ok=True)
            src = Path(result["path"])
            dst = cpa_dir / src.name
            shutil.copy2(src, dst)
            os.chmod(dst, 0o600)
            result["cpa_path"] = str(dst)
            log(f"[cpa] hotload copy -> {dst}")
        except Exception as e:  # noqa: BLE001
            log(f"[cpa] hotload copy failed: {e}")
            result["cpa_copy_error"] = str(e)

    if result.get("ok") and result.get("path") and cfg.get("cpa_management_upload_enabled", False):
        try:
            result["cpa_management_upload"] = upload_cpa_auth_file(
                result["path"], cfg, log_callback=log
            )
        except Exception as e:  # noqa: BLE001
            log(f"[cpa] management upload failed: {e}")
            result["cpa_management_upload_error"] = str(e)
            try:
                mark_cpa_uploaded(result.get("path") or "", ok=False, detail=str(e))
            except Exception:
                pass

    # failure log under register dir
    if not result.get("ok"):
        fail_path = out_dir / "cpa_auth_failed.txt"
        with open(fail_path, "a", encoding="utf-8") as f:
            f.write(f"{email}----{result.get('error') or 'unknown'}----{int(time.time())}\n")
        if cfg.get("cpa_mint_required", False):
            raise RuntimeError(f"CPA mint required but failed: {result.get('error')}")
    elif result.get("path") and cfg.get("sub2api_export_enabled", False):
        try:
            import cpa_to_sub2api

            sub_res = cpa_to_sub2api.export_after_cpa_result(
                result,
                config=cfg,
                log_callback=log,
            )
            result["sub2api"] = sub_res
        except Exception as e:  # noqa: BLE001
            log(f"[sub2api] export failed: {e}")
            result["sub2api_error"] = str(e)

    return result
