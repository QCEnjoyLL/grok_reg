"""Probe free Grok 4.5 via cli-chat-proxy with a CPA access_token."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .proxyutil import resolve_proxy
from .schema import DEFAULT_BASE_URL, DEFAULT_CLIENT_HEADERS


def _normalize_model_id(mid: str) -> str:
    return str(mid or "").strip().lower()


def _is_free_build_model_id(mid: str) -> bool:
    m = _normalize_model_id(mid)
    if not m:
        return False
    # free Build channel historically used grok-4.5; current listing may be grok-build
    if m in {"grok-4.5", "grok-4.5-latest", "grok-build", "grok-build-latest"}:
        return True
    if m.startswith("grok-4.5") or m.startswith("grok-build"):
        return True
    return False


def _is_free_build_model_list(ids: list[Any]) -> bool:
    return any(_is_free_build_model_id(i) for i in (ids or []))


def _opener(proxy: str | None = None) -> urllib.request.OpenerDirector:
    p = resolve_proxy(proxy)
    handlers: list[Any] = []
    if p:
        handlers.append(urllib.request.ProxyHandler({"http": p, "https": p}))
    return urllib.request.build_opener(*handlers) if handlers else urllib.request.build_opener()


def _is_permission_denied(status: int | None, error: str = "") -> bool:
    """True for account/token hard rejects (delete-worthy), not network blips."""
    try:
        code = int(status or 0)
    except Exception:
        code = 0
    if code in (401, 403):
        return True
    blob = str(error or "").lower()
    needles = (
        "permission",
        "forbidden",
        "unauthorized",
        "not allowed",
        "access denied",
        "对话权限",
        "权限被拒",
        "权限拒绝",
    )
    return any(n in blob for n in needles)


def probe_models(
    access_token: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 30.0,
    proxy: str | None = None,
) -> dict[str, Any]:
    base = base_url.rstrip("/")
    url = f"{base}/models"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        **DEFAULT_CLIENT_HEADERS,
    }
    opener = _opener(proxy)
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            ids = [x.get("id") for x in body.get("data") or [] if isinstance(x, dict)]
            return {
                "ok": True,
                "status": getattr(resp, "status", 200),
                "model_ids": ids,
                "has_grok_45": _is_free_build_model_list(ids),
                "permission_denied": False,
            }
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")[:500]
        return {
            "ok": False,
            "status": e.code,
            "error": err,
            "model_ids": [],
            "has_grok_45": False,
            "permission_denied": _is_permission_denied(e.code, err),
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "status": 0,
            "error": str(e),
            "model_ids": [],
            "has_grok_45": False,
            "permission_denied": False,
        }


def probe_mini_response(
    access_token: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 60.0,
    proxy: str | None = None,
    model: str = "grok-build",
) -> dict[str, Any]:
    base = base_url.rstrip("/")
    url = f"{base}/responses"
    # try preferred model then fallbacks used by free build channel
    models = [model, "grok-4.5", "grok-build-latest", "grok-4.5-latest"]
    # de-dupe preserve order
    seen: set[str] = set()
    model_list: list[str] = []
    for m in models:
        m = str(m or "").strip()
        if not m or m in seen:
            continue
        seen.add(m)
        model_list.append(m)

    last: dict[str, Any] = {"ok": False, "status": 0, "error": "no model tried"}
    for mid in model_list:
        payload = {
            "model": mid,
            "stream": False,
            "input": "Reply with exactly MINT_OK",
            "reasoning": {"effort": "low"},
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            **DEFAULT_CLIENT_HEADERS,
        }
        opener = _opener(proxy)
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with opener.open(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                texts: list[str] = []
                for item in body.get("output") or []:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "message":
                        for c in item.get("content") or []:
                            if isinstance(c, dict) and c.get("type") in ("output_text", "text"):
                                texts.append(str(c.get("text") or ""))
                text = "".join(texts).strip()
                return {
                    "ok": True,
                    "status": getattr(resp, "status", 200),
                    "model": mid,
                    "text": text[:200],
                    "permission_denied": False,
                    "unusable": False,  # soft fail - keep file unless caller wants strict mode
                }
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")[:500]
            denied = _is_permission_denied(e.code, err)
            last = {
                "ok": False,
                "status": e.code,
                "model": mid,
                "error": err,
                "permission_denied": denied,
                "unusable": denied,
            }
            # hard permission: stop trying other models
            if denied:
                return last
            continue
        except Exception as e:  # noqa: BLE001
            last = {
                "ok": False,
                "status": 0,
                "model": mid,
                "error": str(e),
                "permission_denied": False,
                "unusable": False,
            }
            continue
    return last


def probe_usability(
    access_token: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    proxy: str | None = None,
    timeout_models: float = 30.0,
    timeout_chat: float = 60.0,
) -> dict[str, Any]:
    """Determine whether a CPA token is usable for free-build chat.

    Unusable (auto-delete candidates):
      - models/chat HTTP 401/403
      - explicit permission errors in body
    Soft issues (NOT auto-delete):
      - network errors
      - models missing free-build ids but chat might still work
      - 429 rate limit
    """
    access = (access_token or "").strip()
    if not access:
        return {
            "usable": False,
            "unusable": True,
            "reason": "missing access_token",
            "status": 0,
            "permission_denied": True,
        }

    models = probe_models(access, base_url=base_url, timeout=timeout_models, proxy=proxy)
    if models.get("permission_denied"):
        return {
            "usable": False,
            "unusable": True,
            "reason": f"models permission denied status={models.get('status')}",
            "status": models.get("status"),
            "permission_denied": True,
            "probe_models": models,
        }

    chat = probe_mini_response(access, base_url=base_url, timeout=timeout_chat, proxy=proxy)
    if chat.get("ok"):
        return {
            "usable": True,
            "unusable": False,
            "reason": "chat ok",
            "status": chat.get("status") or 200,
            "permission_denied": False,
            "probe_models": models,
            "probe_chat": chat,
            "model": chat.get("model"),
        }

    if chat.get("permission_denied") or chat.get("unusable"):
        return {
            "usable": False,
            "unusable": True,
            "reason": f"chat permission denied status={chat.get('status')} model={chat.get('model')}",
            "status": chat.get("status"),
            "permission_denied": True,
            "probe_models": models,
            "probe_chat": chat,
        }

    # chat failed for non-permission reasons: still unusable for practical purposes
    # but only auto-delete on hard permission by default. Mark usable=False soft.
    status = chat.get("status") or models.get("status") or 0
    return {
        "usable": False,
        "unusable": False,  # soft fail — keep file unless caller wants strict mode
        "reason": f"chat probe failed status={status} error={str(chat.get('error') or '')[:200]}",
        "status": status,
        "permission_denied": False,
        "probe_models": models,
        "probe_chat": chat,
    }
