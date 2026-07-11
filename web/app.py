"""Grok Register web dashboard - login gate, settings, jobs, noVNC."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

APP_HOME = Path(os.environ.get("APP_HOME", Path(__file__).resolve().parents[1]))
DATA_DIR = Path(os.environ.get("DATA_DIR", APP_HOME / "data"))
WEB_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))

if str(APP_HOME) not in sys.path:
    sys.path.insert(0, str(APP_HOME))

MAX_LOG_LINES = 4000
DASHBOARD_TITLE = os.environ.get("DASHBOARD_TITLE", "Grok Register")
SETTINGS_FILE = DATA_DIR / "ui_settings.json"
ENV_BOOT_TOKEN = os.environ.get("WEB_TOKEN", "").strip()
ENV_BOOT_NOVNC = os.environ.get("NOVNC_PUBLIC_URL", "").strip()

# Runtime auth token (mutable via UI; persisted under /data)
_token_lock = threading.Lock()
_runtime_token = ENV_BOOT_TOKEN or "change-me"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _load_ui_settings() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    defaults = {
        "web_token": _runtime_token,
        "novnc_public_url": ENV_BOOT_NOVNC,  # e.g. http://host:6089/vnc.html?autoconnect=1&resize=scale
        "novnc_host": "",
        "novnc_port": os.environ.get("NOVNC_HOST_PORT")
        or os.environ.get("NOVNC_PUBLIC_PORT")
        or "",
    }
    if not SETTINGS_FILE.is_file():
        # seed from env once
        save = {
            "web_token": defaults["web_token"],
            "novnc_public_url": defaults["novnc_public_url"],
            "novnc_host": defaults["novnc_host"],
            "novnc_port": str(defaults["novnc_port"] or ""),
        }
        SETTINGS_FILE.write_text(json.dumps(save, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return save
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return defaults
        for k, v in defaults.items():
            data.setdefault(k, v)
        return data
    except Exception:
        return defaults


def _save_ui_settings(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_web_token() -> str:
    with _token_lock:
        return _runtime_token


def set_web_token(token: str) -> None:
    global _runtime_token
    token = (token or "").strip()
    if not token:
        raise ValueError("token 不能为空")
    with _token_lock:
        _runtime_token = token
    s = _load_ui_settings()
    s["web_token"] = token
    _save_ui_settings(s)


def init_runtime_token() -> None:
    global _runtime_token
    s = _load_ui_settings()
    tok = str(s.get("web_token") or ENV_BOOT_TOKEN or "change-me").strip()
    with _token_lock:
        _runtime_token = tok


def build_novnc_url(request: Request | None = None) -> str:
    s = _load_ui_settings()
    full = str(s.get("novnc_public_url") or "").strip()
    if full:
        return full
    host = str(s.get("novnc_host") or "").strip()
    port = str(s.get("novnc_port") or "").strip()
    if not host and request is not None:
        # fall back to request hostname
        host = request.url.hostname or "127.0.0.1"
    if not port:
        # container internal published mapping unknown; default 6080
        port = "6080"
    if host:
        return f"http://{host}:{port}/vnc.html?autoconnect=1&resize=scale"
    return f":{port}/vnc.html?autoconnect=1&resize=scale"


def resolve_config_path() -> Path:
    for p in (DATA_DIR / "config.json", APP_HOME / "config.json", APP_HOME / "config.example.json"):
        if p.is_file():
            return p
    return DATA_DIR / "config.json"


def resolve_accounts_path() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / "accounts_cli.txt"


def resolve_cpa_dir() -> Path:
    cand = DATA_DIR / "cpa_auths"
    cand.mkdir(parents=True, exist_ok=True)
    return cand


def load_config_dict() -> dict[str, Any]:
    path = resolve_config_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if not str(k).startswith("//") and not str(k).startswith("#")}


def mask_secret(value: str, keep: int = 3) -> str:
    s = str(value or "")
    if len(s) <= keep * 2:
        return "*" * len(s) if s else ""
    return s[:keep] + "*" * (len(s) - keep * 2) + s[-keep:]


class JobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.proc: subprocess.Popen[str] | None = None
        self.kind: str | None = None
        self.cmd: list[str] = []
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.exit_code: int | None = None
        self.logs: deque[str] = deque(maxlen=MAX_LOG_LINES)
        self.stats: dict[str, int] = {
            "reg_success": 0,
            "reg_fail": 0,
            "mint_success": 0,
            "mint_fail": 0,
            "mint_skip": 0,
        }

    def append_log(self, line: str) -> None:
        line = line.rstrip("\n")
        if not line:
            return
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {line}"
        with self._lock:
            self.logs.append(entry)
            self._parse_stats(line)

    def _parse_stats(self, line: str) -> None:
        m = re.search(
            r"注册成功\s*(\d+).*注册失败\s*(\d+).*CPA成功\s*(\d+).*CPA失败\s*(\d+).*CPA跳过\s*(\d+)",
            line,
        )
        if m:
            self.stats.update(
                {
                    "reg_success": int(m.group(1)),
                    "reg_fail": int(m.group(2)),
                    "mint_success": int(m.group(3)),
                    "mint_fail": int(m.group(4)),
                    "mint_skip": int(m.group(5)),
                }
            )

    def is_running(self) -> bool:
        with self._lock:
            return self.proc is not None and self.proc.poll() is None

    def status(self) -> dict[str, Any]:
        with self._lock:
            running = self.proc is not None and self.proc.poll() is None
            code = None if self.proc is None else self.proc.poll()
            if code is not None and self.exit_code is None:
                self.exit_code = code
                self.finished_at = self.finished_at or _now_iso()
            return {
                "running": running,
                "kind": self.kind,
                "cmd": self.cmd,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "exit_code": self.exit_code if not running else None,
                "pid": self.proc.pid if self.proc and running else None,
                "stats": dict(self.stats),
                "log_lines": len(self.logs),
            }

    def get_logs(self, tail: int = 200) -> list[str]:
        with self._lock:
            if tail <= 0:
                return list(self.logs)
            return list(self.logs)[-tail:]

    def start(self, kind: str, cmd: list[str], env: dict[str, str] | None = None) -> None:
        with self._lock:
            if self.proc is not None and self.proc.poll() is None:
                raise RuntimeError("已有任务在运行，请先停止")
            run_env = os.environ.copy()
            if env:
                run_env.update(env)
            run_env.setdefault("PYTHONUNBUFFERED", "1")
            run_env.setdefault("DISPLAY", os.environ.get("DISPLAY", ":99"))
            self.logs.clear()
            self.stats = {k: 0 for k in self.stats}
            self.kind = kind
            self.cmd = cmd
            self.started_at = _now_iso()
            self.finished_at = None
            self.exit_code = None
            self.append_log(f"$ {' '.join(cmd)}")
            self.proc = subprocess.Popen(
                cmd,
                cwd=str(APP_HOME),
                env=run_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )

            def _reader() -> None:
                assert self.proc is not None and self.proc.stdout is not None
                try:
                    for line in self.proc.stdout:
                        self.append_log(line)
                finally:
                    code = self.proc.poll()
                    with self._lock:
                        self.exit_code = code
                        self.finished_at = _now_iso()
                    self.append_log(f"[job] exited code={code}")

            threading.Thread(target=_reader, daemon=True, name="job-log-reader").start()

    def stop(self, timeout: float = 15.0) -> bool:
        with self._lock:
            proc = self.proc
        if proc is None or proc.poll() is not None:
            return False
        self.append_log("[job] sending SIGTERM...")
        try:
            proc.send_signal(signal.SIGTERM)
        except Exception:
            proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.append_log("[job] SIGKILL...")
            proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        return True


jobs = JobManager()
app = FastAPI(title=DASHBOARD_TITLE, version="1.1.0")
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
init_runtime_token()


def _extract_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    return (
        bearer
        or request.headers.get("X-Web-Token", "")
        or request.query_params.get("token", "")
        or request.cookies.get("web_token", "")
        or ""
    ).strip()


def _token_ok(got: str) -> bool:
    return bool(got) and got == get_web_token()


def require_auth(request: Request) -> None:
    if not _token_ok(_extract_token(request)):
        raise HTTPException(status_code=401, detail="未登录或 Token 错误")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # public endpoints
    if path in ("/healthz", "/api/health", "/login", "/api/login"):
        return await call_next(request)
    if path.startswith("/static"):
        return await call_next(request)
    # HTML shell: always serve (JS enforces login UI). API needs token.
    if path.startswith("/api") or path.startswith("/ws"):
        # websocket handled separately
        if path.startswith("/api") and path not in ("/api/health", "/api/login"):
            try:
                require_auth(request)
            except HTTPException as e:
                return JSONResponse({"detail": e.detail}, status_code=e.status_code)
    return await call_next(request)


class LoginBody(BaseModel):
    token: str


class RegisterBody(BaseModel):
    extra: int = Field(1, ge=1, le=500)
    threads: int = Field(1, ge=1, le=10)
    mint_workers: int = Field(-1, ge=-1, le=10)
    fast: bool = True
    inline_mint: bool = False


class BackfillBody(BaseModel):
    limit: int = Field(1, ge=0, le=5000)
    email: str = ""
    timeout: int = Field(300, ge=30, le=3600)
    probe: bool = True
    headless: bool = False
    sleep: float = Field(3.0, ge=0, le=120)


class ConfigUpdateBody(BaseModel):
    config: dict[str, Any]


class SettingsUpdateBody(BaseModel):
    web_token: str | None = None
    novnc_public_url: str | None = None
    novnc_host: str | None = None
    novnc_port: str | None = None


@app.get("/healthz")
@app.get("/api/health")
def health():
    return {
        "ok": True,
        "time": _now_iso(),
        "display": os.environ.get("DISPLAY", ""),
        "data_dir": str(DATA_DIR),
        "job_running": jobs.is_running(),
        "auth_required": True,
    }


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "login.html",
        {"title": DASHBOARD_TITLE},
    )


@app.post("/api/login")
def api_login(body: LoginBody):
    token = (body.token or "").strip()
    if not _token_ok(token):
        # allow bootstrap if settings empty? no - compare runtime
        if token != get_web_token():
            raise HTTPException(401, "Token 错误")
    resp = JSONResponse({"ok": True})
    # cookie for page navigations; 30 days
    resp.set_cookie(
        key="web_token",
        value=token,
        httponly=False,
        samesite="lax",
        max_age=30 * 24 * 3600,
    )
    return resp


@app.post("/api/logout")
def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("web_token")
    return resp


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    # gate: no valid cookie/token -> login page
    if not _token_ok(_extract_token(request)):
        return RedirectResponse(url="/login", status_code=302)
    return TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {
            "title": DASHBOARD_TITLE,
            "novnc_url": build_novnc_url(request),
        },
    )


@app.get("/api/me")
def api_me(request: Request):
    require_auth(request)
    s = _load_ui_settings()
    return {
        "ok": True,
        "novnc_public_url": s.get("novnc_public_url") or "",
        "novnc_host": s.get("novnc_host") or "",
        "novnc_port": s.get("novnc_port") or "",
        "novnc_url": build_novnc_url(request),
        "token_hint": mask_secret(get_web_token()),
    }


@app.get("/api/settings")
def api_settings_get(request: Request):
    require_auth(request)
    s = _load_ui_settings()
    return {
        "web_token_hint": mask_secret(str(s.get("web_token") or "")),
        "novnc_public_url": s.get("novnc_public_url") or "",
        "novnc_host": s.get("novnc_host") or "",
        "novnc_port": str(s.get("novnc_port") or ""),
        "novnc_url": build_novnc_url(request),
    }


@app.put("/api/settings")
def api_settings_put(body: SettingsUpdateBody, request: Request):
    require_auth(request)
    s = _load_ui_settings()
    if body.novnc_public_url is not None:
        s["novnc_public_url"] = body.novnc_public_url.strip()
    if body.novnc_host is not None:
        s["novnc_host"] = body.novnc_host.strip()
    if body.novnc_port is not None:
        s["novnc_port"] = str(body.novnc_port).strip()
    token_changed = False
    new_token = None
    if body.web_token is not None:
        nt = body.web_token.strip()
        # ignore unchanged masked value
        if nt and "*" not in nt:
            set_web_token(nt)
            s["web_token"] = nt
            token_changed = True
            new_token = nt
        elif nt and "*" in nt:
            pass  # keep
    else:
        s["web_token"] = get_web_token()
    _save_ui_settings(s)
    resp = {
        "ok": True,
        "token_changed": token_changed,
        "settings": {
            "web_token_hint": mask_secret(get_web_token()),
            "novnc_public_url": s.get("novnc_public_url") or "",
            "novnc_host": s.get("novnc_host") or "",
            "novnc_port": str(s.get("novnc_port") or ""),
            "novnc_url": build_novnc_url(request),
        },
    }
    out = JSONResponse(resp)
    if token_changed and new_token:
        out.set_cookie("web_token", new_token, httponly=False, samesite="lax", max_age=30 * 24 * 3600)
    return out


@app.get("/api/status")
def api_status(request: Request):
    require_auth(request)
    accounts = resolve_accounts_path()
    cpa_dir = resolve_cpa_dir()
    account_count = 0
    if accounts.is_file():
        account_count = sum(
            1 for line in accounts.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip()
        )
    cpa_count = 0
    if cpa_dir.is_dir():
        cpa_count = len(list(cpa_dir.glob("xai-*.json")))
    cfg = load_config_dict()
    return {
        "job": jobs.status(),
        "accounts_count": account_count,
        "cpa_count": cpa_count,
        "accounts_file": str(accounts),
        "cpa_dir": str(cpa_dir),
        "config_path": str(resolve_config_path()),
        "email_provider": cfg.get("email_provider"),
        "proxy_set": bool(cfg.get("proxy")),
        "cpa_export_enabled": bool(cfg.get("cpa_export_enabled", True)),
        "cpa_headless": bool(cfg.get("cpa_headless", False)),
        "display": os.environ.get("DISPLAY", ""),
        "novnc_url": build_novnc_url(request),
        "setup_hints": _setup_hints(cfg),
    }


def _setup_hints(cfg: dict[str, Any]) -> list[str]:
    """Human-friendly missing-config checklist for the dashboard banner."""
    hints: list[str] = []
    provider = str(cfg.get("email_provider") or "cloudflare").strip().lower()

    def _is_placeholder(val: str) -> bool:
        v = (val or "").strip().lower()
        if not v:
            return True
        bad_tokens = (
            "example.com",
            "your-temp-mail",
            "yourdomain",
            "xxxx",
            "your_api_key",
            "127.0.0.1:7890",
            "localhost",
        )
        return any(t in v for t in bad_tokens)

    if provider in ("moemail", "nloln", "mail_nloln"):
        if _is_placeholder(str(cfg.get("moemail_api_key") or "")):
            hints.append('还差第1项：MoeMail API Key (moemail_api_key)')
        # domain optional if API returns domains
        base = str(cfg.get("moemail_api_base") or "https://mail.nloln.cn")
        if _is_placeholder(base):
            hints.append('还差 MoeMail API 地址 moemail_api_base')
    elif provider == "cloudflare":
        base = str(cfg.get("cloudflare_api_base") or "")
        if _is_placeholder(base):
            hints.append('还差第1项：临时邮箱 Worker 地址 cloudflare_api_base')
        domains = str(cfg.get("defaultDomains") or "")
        if _is_placeholder(domains) or domains in ("example.com", "xx.shop"):
            hints.append('还差第2项：真实邮箱域名 defaultDomains')
    elif provider == "cloudmail":
        if _is_placeholder(str(cfg.get("cloudmail_url") or "")):
            hints.append('还差 CloudMail 地址 cloudmail_url')

    proxy = str(cfg.get("proxy") or "")
    if _is_placeholder(proxy) or proxy in ("http://127.0.0.1:7890",):
        hints.append('还差第3项：服务器可访问的 proxy')

    if cfg.get("cpa_export_enabled", True) and bool(cfg.get("cpa_headless", False)):
        hints.append('建议 CPA headless=false')

    if not hints:
        hints.append('关键配置看起来可用，可以开始注册 1 个号试试')
    return hints


@app.get("/api/logs")
def api_logs(request: Request, tail: int = Query(200, ge=1, le=4000)):
    require_auth(request)
    return {"lines": jobs.get_logs(tail)}


@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket, token: str = ""):
    got = token or ws.headers.get("x-web-token") or ""
    # also cookie header
    cookie = ws.headers.get("cookie") or ""
    if "web_token=" in cookie and not got:
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("web_token="):
                got = part.split("=", 1)[1]
    if not _token_ok(got):
        await ws.close(code=4401)
        return
    await ws.accept()
    last = 0
    try:
        while True:
            lines = jobs.get_logs(0)
            if len(lines) > last:
                chunk = lines[last:]
                last = len(lines)
                await ws.send_json({"lines": chunk, "status": jobs.status()})
            else:
                await ws.send_json({"lines": [], "status": jobs.status()})
            await asyncio.sleep(0.8)
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await ws.close()
        except Exception:
            pass


@app.get("/api/accounts")
def api_accounts(request: Request, limit: int = Query(100, ge=1, le=2000), offset: int = 0):
    require_auth(request)
    path = resolve_accounts_path()
    rows = []
    total = 0
    if path.is_file():
        lines = [ln.strip() for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
        total = len(lines)
        for ln in lines[offset : offset + limit]:
            parts = ln.split("----")
            email = parts[0] if parts else ln
            password = parts[1] if len(parts) > 1 else ""
            sso = parts[2] if len(parts) > 2 else ""
            rows.append(
                {
                    "email": email,
                    "password": password,
                    "sso_preview": (sso[:24] + "?") if len(sso) > 24 else sso,
                    "has_sso": bool(sso),
                }
            )
    return {"total": total, "items": rows}


@app.get("/api/cpa")
def api_cpa(request: Request, limit: int = Query(100, ge=1, le=2000)):
    require_auth(request)
    d = resolve_cpa_dir()
    items = []
    if d.is_dir():
        files = sorted(d.glob("xai-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
        for f in files:
            email = f.name[len("xai-") : -len(".json")] if f.name.startswith("xai-") else f.stem
            items.append(
                {
                    "file": f.name,
                    "email": email,
                    "size": f.stat().st_size,
                    "mtime": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
                }
            )
    return {"dir": str(d), "items": items}


@app.get("/api/config")
def api_config_get(request: Request, redact: bool = True):
    require_auth(request)
    path = resolve_config_path()
    if not path.is_file():
        return {"path": str(path), "config": {}, "setup_hints": _setup_hints({})}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {"path": str(path), "config": {}}
    secret_keys = {
        "cloudmail_password",
        "cloudflare_api_key",
        "duckmail_api_key",
        "yyds_api_key",
        "yyds_jwt",
        "proxy",
        "cpa_proxy",
        "grok2api_remote_app_key",
        "cpa_management_key",
    }
    out = {}
    for k, v in raw.items():
        if str(k).startswith("//") or str(k).startswith("#"):
            continue
        if redact and k in secret_keys and isinstance(v, str) and v:
            out[k] = mask_secret(v)
        else:
            out[k] = v
    return {"path": str(path), "config": out, "redacted": redact, "setup_hints": _setup_hints(out)}


@app.put("/api/config")
def api_config_put(body: ConfigUpdateBody, request: Request):
    require_auth(request)
    path = resolve_config_path()
    if path.name == "config.example.json":
        path = DATA_DIR / "config.json"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        except Exception:
            existing = {}
    secret_keys = {
        "cloudmail_password",
        "cloudflare_api_key",
        "duckmail_api_key",
        "yyds_api_key",
        "yyds_jwt",
        "proxy",
        "cpa_proxy",
        "grok2api_remote_app_key",
        "cpa_management_key",
    }
    merged = dict(existing)
    for k, v in (body.config or {}).items():
        if str(k).startswith("//"):
            continue
        if k in secret_keys and isinstance(v, str) and "*" in v and k in existing:
            if mask_secret(str(existing.get(k, ""))) == v:
                continue
        merged[k] = v
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    app_cfg = APP_HOME / "config.json"
    try:
        if app_cfg.exists() or app_cfg.is_symlink():
            if not app_cfg.is_symlink():
                app_cfg.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            # symlink into app home when possible
            try:
                if app_cfg.exists():
                    app_cfg.unlink()
                app_cfg.symlink_to(path)
            except Exception:
                app_cfg.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass
    return {"ok": True, "path": str(path)}


@app.post("/api/jobs/register")
def api_job_register(body: RegisterBody, request: Request):
    require_auth(request)
    if jobs.is_running():
        raise HTTPException(409, "任务运行中")
    cmd = [
        sys.executable,
        "-u",
        str(APP_HOME / "register_cli.py"),
        "--extra",
        str(body.extra),
        "--threads",
        str(body.threads),
        "--mint-workers",
        str(body.mint_workers),
        "--accounts-file",
        str(resolve_accounts_path()),
    ]
    if not body.fast:
        cmd.append("--no-fast")
    if body.inline_mint:
        cmd.append("--inline-mint")
    jobs.start("register", cmd)
    return {"ok": True, "job": jobs.status()}


@app.post("/api/jobs/backfill")
def api_job_backfill(body: BackfillBody, request: Request):
    require_auth(request)
    if jobs.is_running():
        raise HTTPException(409, "任务运行中")
    script = APP_HOME / "scripts" / "backfill_cpa_xai_from_accounts.py"
    cmd = [
        sys.executable,
        "-u",
        str(script),
        "--limit",
        str(body.limit),
        "--timeout",
        str(body.timeout),
        "--sleep",
        str(body.sleep),
        "--out-dir",
        str(resolve_cpa_dir()),
    ]
    if body.probe:
        cmd.append("--probe")
    if body.headless:
        cmd.append("--headless")
    if body.email.strip():
        cmd.extend(["--email", body.email.strip()])
    jobs.start("backfill", cmd)
    return {"ok": True, "job": jobs.status()}


@app.post("/api/jobs/stop")
def api_job_stop(request: Request):
    require_auth(request)
    stopped = jobs.stop()
    return {"ok": True, "stopped": stopped, "job": jobs.status()}


@app.get("/api/download/accounts")
def download_accounts(request: Request):
    require_auth(request)
    path = resolve_accounts_path()
    if not path.is_file():
        raise HTTPException(404, "accounts file not found")
    return FileResponse(path, filename="accounts_cli.txt", media_type="text/plain")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grok Register web dashboard")
    parser.add_argument("--host", default=os.environ.get("WEB_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("WEB_PORT", "8080")))
    args = parser.parse_args(argv)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "cpa_auths").mkdir(parents=True, exist_ok=True)
    init_runtime_token()
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
