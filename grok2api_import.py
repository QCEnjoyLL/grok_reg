"""Bulk import SSO accounts into Grok2API pools.

Supports two remote styles:
1) classic grok2api admin API (this project default):
     POST {base}/tokens/add?app_key=...  pool=basic|super
2) optional lite-style admin (login + multipart):
     POST {base}/api/admin/v1/auth/login
     POST {base}/api/admin/v1/accounts/web/import   (SSO txt)
     POST {base}/api/admin/v1/accounts/import       (CPA xai json)

Does not change registration flow; used by dashboard manual import.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

LogFn = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def _normalize_sso(raw: str) -> str:
    token = str(raw or "").strip()
    if token.lower().startswith("sso="):
        token = token.split("=", 1)[1].strip()
    return token.strip().strip('"').strip("'")


def _load_accounts(path: Path, *, emails: set[str] | None = None, require_sso: bool = True) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    out: list[dict[str, str]] = []
    wanted = {e.lower() for e in (emails or set()) if e}
    for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split("----")
        if len(parts) < 2:
            continue
        email = parts[0].strip()
        password = parts[1].strip()
        sso = parts[2].strip() if len(parts) > 2 else ""
        if len(parts) > 3:
            password = parts[1].strip()
            sso = "----".join(p.strip() for p in parts[2:]).strip()
        sso = _normalize_sso(sso)
        if wanted and email.lower() not in wanted:
            continue
        if require_sso and not sso:
            continue
        if not email:
            continue
        out.append({"email": email, "password": password, "sso": sso})
    return out


def _pool_remote_name(pool_name: str) -> str:
    m = {"ssobasic": "basic", "basic": "basic", "ssosuper": "super", "super": "super"}
    return m.get(str(pool_name or "").strip().lower(), "basic")


def _http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict | list | None = None,
    data: bytes | None = None,
    timeout: float = 30.0,
    query: dict[str, str] | None = None,
) -> tuple[int, Any, str]:
    if query:
        q = urllib.parse.urlencode(query)
        url = url + ("&" if "?" in url else "?") + q
    raw_body = data
    hdrs = {"Accept": "application/json", "User-Agent": "grok-reg/grok2api-import"}
    if headers:
        hdrs.update(headers)
    if body is not None:
        raw_body = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=raw_body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
        status = e.code
    parsed: Any = None
    if text.strip():
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
    return int(status), parsed, text


def import_sso_via_tokens_add(
    accounts: list[dict[str, str]],
    *,
    base_url: str,
    app_key: str,
    pool_name: str = "ssoBasic",
    batch_size: int = 50,
    log: LogFn | None = None,
    stop_event=None,
) -> dict[str, Any]:
    """Classic grok2api: POST /tokens/add with app_key."""
    log = log or _noop
    base = (base_url or "").strip().rstrip("/")
    key = (app_key or "").strip()
    if not base or not key:
        return {"ok": False, "error": "未配置 grok2api_remote_base / grok2api_remote_app_key"}
    if not accounts:
        return {"ok": False, "error": "没有可导入的 SSO 账号", "imported": 0}

    remote_pool = _pool_remote_name(pool_name)
    batch_size = max(1, min(200, int(batch_size or 50)))
    tokens = []
    meta = []
    seen: set[str] = set()
    for a in accounts:
        sso = _normalize_sso(a.get("sso") or "")
        if not sso or sso in seen:
            continue
        seen.add(sso)
        tokens.append(sso)
        meta.append(a.get("email") or "")

    imported = 0
    failed: list[dict[str, Any]] = []
    batches = 0
    for i in range(0, len(tokens), batch_size):
        if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
            break
        chunk = tokens[i : i + batch_size]
        emails = meta[i : i + batch_size]
        batches += 1
        payload = {
            "tokens": chunk,
            "pool": remote_pool,
            "tags": ["grok-reg-import"],
        }
        # also send structured entries when supported
        payload["items"] = [
            {"token": t, "tags": ["grok-reg-import"], "note": em}
            for t, em in zip(chunk, emails)
        ]
        status, parsed, text = _http_json(
            "POST",
            f"{base}/tokens/add",
            body=payload,
            query={"app_key": key, "auto_nsfw": "true"},
            timeout=60.0,
        )
        if status >= 400:
            # fallback without items
            status2, parsed2, text2 = _http_json(
                "POST",
                f"{base}/tokens/add",
                body={"tokens": chunk, "pool": remote_pool, "tags": ["grok-reg-import"]},
                query={"app_key": key, "auto_nsfw": "true"},
                timeout=60.0,
            )
            if status2 >= 400:
                failed.append(
                    {
                        "batch": batches,
                        "count": len(chunk),
                        "status": status2,
                        "error": (text2 or text)[:300],
                        "emails": emails[:20],
                    }
                )
                log(f"[g2a] batch {batches} failed HTTP {status2}: {(text2 or text)[:120]}")
                continue
            status, parsed, text = status2, parsed2, text2
        imported += len(chunk)
        log(f"[g2a] batch {batches} ok +{len(chunk)} pool={pool_name}/{remote_pool}")

    return {
        "ok": failed == [] and imported > 0,
        "mode": "tokens_add",
        "base_url": base,
        "pool": pool_name,
        "remote_pool": remote_pool,
        "total_sso": len(tokens),
        "imported": imported,
        "batches": batches,
        "failed_batches": failed,
        "error": None if not failed else f"{len(failed)} batch(es) failed",
    }


def grok2api_admin_login(base_url: str, username: str, password: str, *, timeout: float = 30.0) -> str:
    base = (base_url or "").strip().rstrip("/")
    user = (username or "").strip() or "admin"
    pwd = str(password or "")
    if not base:
        raise ValueError("base_url empty")
    if not pwd.strip() or set(pwd.strip()) == {"*"}:
        raise ValueError("admin password empty or masked")
    status, parsed, text = _http_json(
        "POST",
        f"{base}/api/admin/v1/auth/login",
        body={"username": user, "password": pwd},
        timeout=timeout,
    )
    if status >= 400:
        raise RuntimeError(f"login HTTP {status}: {text[:300]}")
    token = ""
    if isinstance(parsed, dict):
        token = str((((parsed.get("data") or {}).get("tokens") or {}).get("accessToken")) or "")
    if not token:
        raise RuntimeError("login ok but no accessToken")
    return token


def import_sso_via_admin_web(
    accounts: list[dict[str, str]],
    *,
    base_url: str,
    username: str,
    password: str,
    log: LogFn | None = None,
) -> dict[str, Any]:
    """Lite-style: multipart SSO txt to /api/admin/v1/accounts/web/import."""
    log = log or _noop
    lines = []
    seen: set[str] = set()
    for a in accounts:
        sso = _normalize_sso(a.get("sso") or "")
        if sso and sso not in seen:
            seen.add(sso)
            lines.append(sso)
    if not lines:
        return {"ok": False, "error": "没有可导入的 SSO", "imported": 0}
    token = grok2api_admin_login(base_url, username, password)
    boundary = f"----grok-reg-sso-{int(time.time())}"
    filename = f"grok-reg-sso-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    ).encode("utf-8") + payload + f"\r\n--{boundary}--\r\n".encode("utf-8")
    base = base_url.rstrip("/")
    status, parsed, text = _http_json(
        "POST",
        f"{base}/api/admin/v1/accounts/web/import",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        data=body,
        timeout=120.0,
    )
    if status >= 400:
        return {"ok": False, "error": f"web/import HTTP {status}: {text[:300]}", "imported": 0, "mode": "admin_web_sso"}
    log(f"[g2a] admin web/import ok sso={len(lines)}")
    return {
        "ok": True,
        "mode": "admin_web_sso",
        "imported": len(lines),
        "total_sso": len(lines),
        "base_url": base,
        "response": parsed if isinstance(parsed, dict) else {"raw": text[:500]},
    }


def import_cpa_files_via_admin(
    files: list[Path],
    *,
    base_url: str,
    username: str,
    password: str,
    log: LogFn | None = None,
) -> dict[str, Any]:
    """Lite-style: multipart xai-*.json to /api/admin/v1/accounts/import."""
    log = log or _noop
    paths = [Path(p) for p in files if Path(p).is_file()]
    if not paths:
        return {"ok": False, "error": "没有可导入的 CPA 文件", "files": 0}
    token = grok2api_admin_login(base_url, username, password)
    boundary = f"----grok-reg-cpa-{int(time.time())}"
    chunks: list[bytes] = []
    for p in paths:
        data = p.read_bytes()
        name = p.name
        part = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="files"; filename="{name}"\r\n'
            f"Content-Type: application/json\r\n\r\n"
        ).encode("utf-8") + data + b"\r\n"
        chunks.append(part)
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(chunks)
    base = base_url.rstrip("/")
    status, parsed, text = _http_json(
        "POST",
        f"{base}/api/admin/v1/accounts/import",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        data=body,
        timeout=300.0,
    )
    if status >= 400:
        return {"ok": False, "error": f"accounts/import HTTP {status}: {text[:300]}", "files": 0, "mode": "admin_cpa"}
    log(f"[g2a] admin accounts/import ok files={len(paths)}")
    return {
        "ok": True,
        "mode": "admin_cpa",
        "files": len(paths),
        "base_url": base,
        "response": parsed if isinstance(parsed, dict) else {"raw": text[:500]},
    }


def resolve_import_mode(cfg: dict[str, Any]) -> str:
    """tokens_add | admin_web_sso | admin_cpa."""
    mode = str(cfg.get("grok2api_import_mode") or cfg.get("grok2api_upload_mode") or "").strip().lower()
    if mode in {"tokens_add", "sso_pool", "pool", "app_key"}:
        return "tokens_add"
    if mode in {"admin_web_sso", "web_sso", "sso"}:
        return "admin_web_sso"
    if mode in {"admin_cpa", "build_auth_files", "cpa", "auth_files"}:
        return "admin_cpa"
    # auto: prefer app_key classic if configured
    if str(cfg.get("grok2api_remote_app_key") or "").strip() and str(cfg.get("grok2api_remote_base") or "").strip():
        return "tokens_add"
    if str(cfg.get("grok2api_admin_password") or cfg.get("grok2api_password") or "").strip():
        return "admin_web_sso"
    return "tokens_add"


def import_from_config(
    cfg: dict[str, Any],
    *,
    accounts_file: str | Path,
    cpa_dir: str | Path | None = None,
    emails: list[str] | None = None,
    limit: int = 0,
    mode: str = "",
    log: LogFn | None = None,
    stop_event=None,
) -> dict[str, Any]:
    log = log or _noop
    cfg = cfg or {}
    mode = (mode or resolve_import_mode(cfg)).strip().lower()
    wanted = {e.strip().lower() for e in (emails or []) if str(e).strip()} or None
    accs = _load_accounts(Path(accounts_file), emails=wanted, require_sso=(mode != "admin_cpa"))
    if limit and limit > 0:
        accs = accs[:limit]
    log(f"[g2a] import mode={mode} accounts={len(accs)}")

    if mode == "tokens_add":
        return import_sso_via_tokens_add(
            accs,
            base_url=str(cfg.get("grok2api_remote_base") or ""),
            app_key=str(cfg.get("grok2api_remote_app_key") or ""),
            pool_name=str(cfg.get("grok2api_pool_name") or "ssoBasic"),
            batch_size=int(cfg.get("grok2api_import_batch_size") or 50),
            log=log,
            stop_event=stop_event,
        )

    base = str(cfg.get("grok2api_admin_base") or cfg.get("grok2api_remote_base") or "").strip()
    user = str(cfg.get("grok2api_admin_username") or cfg.get("grok2api_username") or "admin").strip() or "admin"
    pwd = str(cfg.get("grok2api_admin_password") or cfg.get("grok2api_password") or "").strip()

    if mode == "admin_web_sso":
        return import_sso_via_admin_web(accs, base_url=base, username=user, password=pwd, log=log)

    if mode == "admin_cpa":
        d = Path(cpa_dir or cfg.get("cpa_auth_dir") or "./cpa_auths")
        files = sorted(d.glob("xai-*.json")) if d.is_dir() else []
        if wanted:
            files = [f for f in files if any(w in f.name.lower() for w in wanted)]
        if limit and limit > 0:
            files = files[:limit]
        return import_cpa_files_via_admin(files, base_url=base, username=user, password=pwd, log=log)

    return {"ok": False, "error": f"unknown mode: {mode}"}
