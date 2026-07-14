"""Grok Register web dashboard - login gate, settings, jobs, noVNC."""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
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
BUILD_ID = os.environ.get("BUILD_ID", "v1.0.0")
APP_VERSION = os.environ.get("APP_VERSION", "1.0.0")
SETTINGS_FILE = DATA_DIR / "ui_settings.json"
ENV_BOOT_TOKEN = os.environ.get("WEB_TOKEN", "").strip()
ENV_BOOT_NOVNC = os.environ.get("NOVNC_PUBLIC_URL", "").strip()

# Runtime auth token (mutable via UI; persisted under /data)
_token_lock = threading.Lock()
_runtime_token = ENV_BOOT_TOKEN or "change-me"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

def _coerce_ui_bools(cfg: dict[str, Any]) -> dict[str, Any]:
    bool_keys = {
        "enable_nsfw",
        "cpa_export_enabled",
        "cpa_headless",
        "cpa_management_upload_enabled",
        "resin_sticky_enabled",
        "grok2api_auto_add_local",
        "grok2api_auto_add_remote",
    }
    out = dict(cfg)
    for k in bool_keys:
        if k not in out:
            continue
        v = out[k]
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("true", "1", "yes", "on", "开"):
                out[k] = True
            elif s in ("false", "0", "no", "off", "关", ""):
                out[k] = False
    return out



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


def _fmt_beijing(ts: float | int | str | None = None) -> str:
    """Format timestamp/ISO string as Asia/Shanghai wall time."""
    if ts is None or ts == "":
        return ""
    try:
        from zoneinfo import ZoneInfo

        bj = ZoneInfo("Asia/Shanghai")
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(float(ts), tz=bj)
        else:
            s = str(ts).strip()
            if not s:
                return ""
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
            dt = dt.astimezone(bj)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)[:19].replace("T", " ")

def resolve_cpa_dir() -> Path:
    cand = DATA_DIR / "cpa_auths"
    cand.mkdir(parents=True, exist_ok=True)
    return cand

def resolve_accounts_backup_dir() -> Path:
    d = DATA_DIR / "accounts_cli_backup"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_cpa_backup_dir() -> Path:
    d = DATA_DIR / "cpa_file_backup"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _zip_directory(root: Path, *, prefix: str = "") -> bytes:
    """Zip files under root (files + one-level subdirs)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if not root.is_dir():
            return buf.getvalue()
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            # skip junk
            if p.name.startswith(".") and p.suffix == ".tmp":
                continue
            arc = p.relative_to(root).as_posix()
            if prefix:
                arc = f"{prefix.rstrip('/')}/{arc}"
            zf.write(p, arcname=arc)
    return buf.getvalue()


def _run_backfill_sync(*, timeout_sec: int = 3600) -> dict[str, Any]:
    """Run missing-CPA backfill in-process wait (blocks request thread)."""
    script = APP_HOME / "scripts" / "backfill_cpa_xai_from_accounts.py"
    if not script.is_file():
        raise HTTPException(500, f"backfill script missing: {script}")
    # Prefer DATA_DIR accounts; script default is APP_HOME (symlinked in Docker)
    accounts = resolve_accounts_path()
    out_dir = resolve_cpa_dir()
    cmd = [
        sys.executable,
        "-u",
        str(script),
        "--accounts",
        str(accounts),
        "--out-dir",
        str(out_dir),
        "--limit",
        "0",
        "--timeout",
        "300",
        "--sleep",
        "2",
        "--probe",
    ]
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    # If a dashboard job is running, refuse to double-run browsers
    if jobs.is_running():
        raise HTTPException(409, "有任务正在运行，请先停止后再归档账号")
    proc = subprocess.run(
        cmd,
        cwd=str(APP_HOME),
        env=env,
        capture_output=True,
        text=True,
        timeout=max(60, int(timeout_sec)),
    )
    tail = ((proc.stdout or "") + "\n" + (proc.stderr or ""))[-2000:]
    return {"ok": proc.returncode == 0, "code": proc.returncode, "log_tail": tail}


def archive_accounts_file(*, run_backfill: bool = True, backfill_timeout: int = 3600) -> dict[str, Any]:
    """Backfill missing CPA (optional), then move accounts_cli.txt into backup and recreate empty file."""
    accounts = resolve_accounts_path()
    backup_dir = resolve_accounts_backup_dir()
    result: dict[str, Any] = {
        "ok": True,
        "accounts_file": str(accounts),
        "backup_dir": str(backup_dir),
    }
    line_count = 0
    if accounts.is_file():
        line_count = sum(1 for ln in accounts.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip())
    result["account_count"] = line_count

    if run_backfill and line_count > 0:
        try:
            bf = _run_backfill_sync(timeout_sec=backfill_timeout)
            result["backfill"] = {"ok": bf.get("ok"), "code": bf.get("code")}
            if not bf.get("ok"):
                result["backfill_warning"] = "补生成 CPA 未完全成功，仍继续归档账号文件"
                result["backfill_log_tail"] = bf.get("log_tail")
        except subprocess.TimeoutExpired:
            result["backfill"] = {"ok": False, "code": -1}
            result["backfill_warning"] = "补生成 CPA 超时，仍继续归档账号文件"
        except HTTPException:
            raise
        except Exception as e:
            result["backfill"] = {"ok": False, "error": str(e)}
            result["backfill_warning"] = f"补生成 CPA 异常: {e}；仍继续归档"

    if not accounts.is_file() and line_count == 0:
        accounts.write_text("", encoding="utf-8")
        result["archived"] = False
        result["message"] = "账号文件为空，已确保存在空 accounts_cli.txt"
        return result

    stamp = _stamp()
    dest = backup_dir / f"accounts_cli_{stamp}.txt"
    # move (or copy+truncate if cross-device oddities)
    if accounts.is_file():
        shutil.move(str(accounts), str(dest))
    # recreate empty active file
    accounts.write_text("", encoding="utf-8")
    result["archived"] = True
    result["backup_file"] = dest.name
    result["backup_path"] = str(dest)
    return result


def archive_cpa_files() -> dict[str, Any]:
    """Move current xai-*.json into dated subfolder under cpa_file_backup."""
    cpa_dir = resolve_cpa_dir()
    backup_root = resolve_cpa_backup_dir()
    files = sorted(cpa_dir.glob("xai-*.json")) if cpa_dir.is_dir() else []
    stamp = _stamp()
    batch_dir = backup_root / f"cpa_{stamp}"
    moved = []
    if files:
        batch_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            dest = batch_dir / f.name
            shutil.move(str(f), str(dest))
            moved.append(f.name)
        # prune upload state for moved files
        state_path = cpa_dir / ".upload_state.json"
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if isinstance(state, dict):
                    for name in moved:
                        state.pop(name, None)
                    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except Exception:
                pass
    return {
        "ok": True,
        "archived": bool(moved),
        "moved_count": len(moved),
        "backup_dir": str(backup_root),
        "batch_dir": str(batch_dir) if moved else "",
        "batch_name": batch_dir.name if moved else "",
        "files": moved[:50],
        "message": "没有可归档的 CPA 文件" if not moved else f"已归档 {len(moved)} 个文件",
    }


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
        self._lock = threading.RLock()
        self.proc: subprocess.Popen[str] | None = None
        self.kind: str | None = None
        self.cmd: list[str] = []
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.exit_code: int | None = None
        self.logs: deque[str] = deque(maxlen=MAX_LOG_LINES)
        self._log_epoch: int = 0
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
                "log_epoch": self._log_epoch,
            }

    def get_logs(self, tail: int = 200) -> list[str]:
        with self._lock:
            if tail <= 0:
                return list(self.logs)
            return list(self.logs)[-tail:]

    def start(self, kind: str, cmd: list[str], env: dict[str, str] | None = None) -> None:
        # NOTE: must not call append_log while holding a non-reentrant lock.
        # Use RLock + prepare env outside critical section where possible.
        with self._lock:
            if self.proc is not None and self.proc.poll() is None:
                raise RuntimeError("已有任务在运行，请先停止")
            run_env = os.environ.copy()
            if env:
                run_env.update(env)
            run_env.setdefault("PYTHONUNBUFFERED", "1")
            run_env.setdefault("DISPLAY", os.environ.get("DISPLAY", ":99"))
            self.logs.clear()
            self._log_epoch += 1
            self.stats = {k: 0 for k in self.stats}
            self.kind = kind
            self.cmd = list(cmd)
            self.started_at = _now_iso()
            self.finished_at = None
            self.exit_code = None
            cmd_line = "$ " + " ".join(cmd)
            ts = datetime.now().strftime("%H:%M:%S")
            self.logs.append(f"[{ts}] {cmd_line}")

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
            proc = self.proc

        def _reader() -> None:
            assert proc is not None and proc.stdout is not None
            try:
                for line in proc.stdout:
                    self.append_log(line)
            finally:
                code = proc.poll()
                with self._lock:
                    self.exit_code = code
                    self.finished_at = _now_iso()
                self.append_log(f"[job] exited code={code}")

        threading.Thread(target=_reader, daemon=True, name="job-log-reader").start()

    def stop(self, timeout: float = 8.0) -> bool:
        with self._lock:
            proc = self.proc
        if proc is None or proc.poll() is not None:
            return False
        self.append_log("[job] sending SIGTERM...")
        try:
            proc.send_signal(signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass

        def _wait_kill() -> None:
            try:
                proc.wait(timeout=timeout)
            except Exception:
                self.append_log("[job] SIGKILL...")
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=3)
                except Exception:
                    pass

        # do not block API forever
        threading.Thread(target=_wait_kill, daemon=True, name="job-stop-wait").start()
        return True


jobs = JobManager()

_pending_lock = threading.Lock()
_pending_job: dict[str, Any] | None = None


def enqueue_job_request(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Start a job; return quickly even if process spawn is slow."""
    action = (action or "start").strip().lower()
    payload = dict(payload or {})

    # stop is cheap and should be sync
    if action in ("stop", "job_stop"):
        return _handle_task_action(action, payload)

    result: dict[str, Any] = {"ok": False}
    err: list[Exception] = []

    def _run() -> None:
        try:
            result.update(_handle_task_action(action, payload))
            result["ok"] = True
        except Exception as exc:  # noqa: BLE001
            err.append(exc)
            try:
                jobs.append_log(f"[job] enqueue error: {exc}")
            except Exception:
                pass

    t = threading.Thread(target=_run, daemon=True, name="job-enqueue")
    t.start()
    t.join(timeout=2.5)
    if err:
        e = err[0]
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(500, str(e))
    if t.is_alive():
        return {"ok": True, "queued": True, "job": jobs.status(), "action": action}
    if result.get("ok"):
        return result
    return {"ok": True, "job": jobs.status(), "action": action}


def _pending_job_worker() -> None:
    """Pick up pending_job.json written by external tools / partial clients."""
    while True:
        try:
            drop = DATA_DIR / "pending_job.json"
            if drop.is_file() and not jobs.is_running():
                try:
                    data = json.loads(drop.read_text(encoding="utf-8"))
                except Exception:
                    data = None
                if isinstance(data, dict) and data.get("action"):
                    try:
                        _handle_task_action(str(data.get("action")), dict(data.get("payload") or {}))
                    except Exception as exc:
                        jobs.append_log(f"[job] pending worker: {exc}")
                    try:
                        drop.unlink(missing_ok=True)
                    except Exception:
                        pass
        except Exception:
            pass
        threading.Event().wait(1.0)


threading.Thread(target=_pending_job_worker, daemon=True, name="pending-job-worker").start()

app = FastAPI(title=DASHBOARD_TITLE, version="1.0.0")
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

@app.middleware("http")
async def no_cache_assets(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith(".html") or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response

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
        "build": BUILD_ID,
        "version": APP_VERSION,
    }


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "login.html",
        {"title": DASHBOARD_TITLE, "build": BUILD_ID, "version": APP_VERSION},
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
            "build": BUILD_ID,
            "version": APP_VERSION,
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
        base = str(cfg.get("cloudflare_api_base") or "").strip()
        if _is_placeholder(base):
            hints.append('还差第1项：临时邮箱 Worker 地址 cloudflare_api_base（填 workers.dev，不要填前端 Pages 域名）')
        elif base and ("workers.dev" not in base.lower()) and not base.lower().endswith(("/api", "/admin")):
            # Pages/自定义前端域名很常见被误填；提醒用户改成 Worker API
            hints.append('cloudflare_api_base 建议填 Worker API（*.workers.dev），不要填前端页面域名')
        domains = str(cfg.get("defaultDomains") or "")
        # /api/new_address 可不带 domain；有 domain 更稳
        if domains and (_is_placeholder(domains) or domains in ("example.com", "xx.shop")):
            hints.append('defaultDomains 请填真实邮箱域名')
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


def _ws_extract_token(ws: WebSocket, token: str = "") -> str:
    got = token or ws.headers.get("x-web-token") or ""
    cookie = ws.headers.get("cookie") or ""
    if "web_token=" in cookie and not got:
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("web_token="):
                got = part.split("=", 1)[1]
    return (got or "").strip()


def _handle_task_action(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Start/stop/backfill jobs from REST or WebSocket (shared)."""
    action = (action or "").strip().lower()
    if action in ("start", "run", "register", "job_start"):
        body = RegisterBody(
            extra=int(payload.get("extra", 1) or 1),
            threads=int(payload.get("threads", 1) or 1),
            mint_workers=int(payload.get("mint_workers", -1) if payload.get("mint_workers") is not None else -1),
            fast=bool(payload.get("fast", True)),
            inline_mint=bool(payload.get("inline_mint", False)),
        )
        return _start_register_job(body)
    if action in ("backfill", "job_backfill"):
        body = BackfillBody(
            limit=int(payload["limit"]) if payload.get("limit") is not None else 0,
            email=str(payload.get("email", "") or ""),
            timeout=int(payload.get("timeout", 300) or 300),
            probe=bool(payload.get("probe", True)),
            headless=bool(payload.get("headless", False)),
            sleep=float(payload.get("sleep", 3.0) or 3.0),
        )
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
    if action in ("stop", "job_stop"):
        stopped = jobs.stop()
        return {"ok": True, "stopped": stopped, "job": jobs.status()}
    raise HTTPException(400, f"unknown action: {action}")


@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket, token: str = ""):
    got = _ws_extract_token(ws, token)
    if not _token_ok(got):
        await ws.close(code=4401)
        return
    await ws.accept()
    last = 0
    last_epoch = -1

    async def _pump_logs() -> None:
        nonlocal last, last_epoch
        while True:
            st = jobs.status()
            epoch = int(st.get("log_epoch") or 0)
            lines = jobs.get_logs(0)
            # 新任务 clear logs 会 bump epoch；重置游标，避免中途再开任务看不到实时日志
            if epoch != last_epoch:
                last_epoch = epoch
                last = 0
                await ws.send_json({
                    "type": "logs",
                    "reset": True,
                    "lines": lines,
                    "status": st,
                })
                last = len(lines)
            elif len(lines) > last:
                chunk = lines[last:]
                last = len(lines)
                await ws.send_json({"type": "logs", "lines": chunk, "status": st})
            else:
                await ws.send_json({"type": "ping", "lines": [], "status": st})
            await asyncio.sleep(0.4)

    async def _recv_cmds() -> None:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                await ws.send_json({"type": "error", "ok": False, "detail": "invalid json"})
                continue
            op = str(msg.get("op") or msg.get("action") or "").strip().lower()
            if op in ("ping", "hello"):
                await ws.send_json({"type": "pong", "status": jobs.status()})
                continue
            if not op:
                await ws.send_json({"type": "error", "ok": False, "detail": "missing op"})
                continue
            try:
                # job start can be sync/blocking briefly; run in thread
                result = await asyncio.to_thread(_handle_task_action, op, msg)
                await ws.send_json({"type": "ack", "op": op, **result})
            except HTTPException as he:
                await ws.send_json(
                    {
                        "type": "error",
                        "ok": False,
                        "op": op,
                        "detail": he.detail,
                        "status_code": he.status_code,
                    }
                )
            except Exception as exc:
                await ws.send_json({"type": "error", "ok": False, "op": op, "detail": str(exc)})

    try:
        await asyncio.gather(_pump_logs(), _recv_cmds())
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await ws.close()
        except Exception:
            pass

@app.get("/api/accounts")
def api_accounts(
    request: Request,
    limit: int = Query(20, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
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
                    "sso_preview": (sso[:20] + "...") if len(sso) > 20 else sso,
                    "has_sso": bool(sso),
                }
            )
    page = (offset // limit) + 1 if limit else 1
    pages = max(1, (total + limit - 1) // limit) if limit else 1
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "page": page,
        "pages": pages,
        "items": rows,
    }


@app.get("/api/cpa")
def api_cpa(
    request: Request,
    limit: int = Query(20, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    status: str = Query("all"),
    q: str = Query(""),
):
    """List local CPA auth files with CPAMC upload status + filters.

    status: all | uploaded | pending
    q: email / filename keyword
    """
    require_auth(request)
    d = resolve_cpa_dir()
    files = []
    if d.is_dir():
        files = sorted(d.glob("xai-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

    # local upload ledger written after successful management upload
    upload_state: dict = {}
    state_path = d / ".upload_state.json"
    if state_path.is_file():
        try:
            raw_state = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(raw_state, dict):
                upload_state = raw_state
        except Exception:
            upload_state = {}

    status_key = str(status or "all").strip().lower()
    if status_key in ("yes", "true", "1", "ok", "done"):
        status_key = "uploaded"
    if status_key in ("no", "false", "0", "missing", "not_uploaded", "unuploaded"):
        status_key = "pending"
    if status_key not in ("all", "uploaded", "pending"):
        status_key = "all"
    query = str(q or "").strip().lower()

    items_all = []
    uploaded_count = 0
    pending_count = 0
    for f in files:
        email = f.name[len("xai-") : -len(".json")] if f.name.startswith("xai-") else f.stem
        st = upload_state.get(f.name) if isinstance(upload_state.get(f.name), dict) else {}
        uploaded = bool(st.get("uploaded"))
        if uploaded:
            uploaded_count += 1
        else:
            pending_count += 1
        st_mtime = f.stat().st_mtime
        row = {
            "file": f.name,
            "email": email,
            "size": f.stat().st_size,
            "mtime": _fmt_beijing(st_mtime),
            "mtime_ts": st_mtime,
            "uploaded": uploaded,
            "upload_status": "uploaded" if uploaded else "pending",
            "uploaded_at": _fmt_beijing(st.get("at") or "") if uploaded else "",
            "upload_detail": st.get("detail") or "",
        }
        if status_key == "uploaded" and not uploaded:
            continue
        if status_key == "pending" and uploaded:
            continue
        if query and query not in email.lower() and query not in f.name.lower():
            continue
        items_all.append(row)

    total = len(items_all)
    page_items = items_all[offset : offset + limit]
    page = (offset // limit) + 1 if limit else 1
    pages = max(1, (total + limit - 1) // limit) if limit else 1
    return {
        "dir": str(d),
        "total": total,
        "total_all": len(files),
        "uploaded_count": uploaded_count,
        "pending_count": pending_count,
        "limit": limit,
        "offset": offset,
        "page": page,
        "pages": pages,
        "status": status_key,
        "q": q,
        "items": page_items,
    }



class CpaUploadBody(BaseModel):
    """Manual upload of local CPA auth files to CPAMC management API."""

    file: str | None = None
    files: list[str] | None = None
    pending_only: bool = False
    force: bool = True  # re-upload even if already marked uploaded
    limit: int | None = Field(default=None, ge=1, le=2000)
    workers: int = Field(default=4, ge=1, le=16)


@app.post("/api/cpa/upload")
def api_cpa_upload(request: Request, body: CpaUploadBody):
    """Upload one/more local CPA auth JSON files to CPA management (CPAMC)."""
    require_auth(request)
    cfg = load_config_dict()
    base = str(cfg.get("cpa_management_base") or "").strip()
    key = str(cfg.get("cpa_management_key") or "").strip()
    if not base or not key:
        raise HTTPException(
            status_code=400,
            detail="未配置 cpa_management_base / cpa_management_key，请先在「必要配置」保存 CPA 管理地址和密码",
        )

    d = resolve_cpa_dir()
    upload_state: dict = {}
    state_path = d / ".upload_state.json"
    if state_path.is_file():
        try:
            raw_state = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(raw_state, dict):
                upload_state = raw_state
        except Exception:
            upload_state = {}

    names: list[str] = []
    if body.file:
        names.append(str(body.file).strip())
    if body.files:
        names.extend(str(x).strip() for x in body.files if str(x).strip())

    if names:
        seen: set[str] = set()
        uniq: list[str] = []
        for n in names:
            key_name = Path(n).name
            if key_name and key_name not in seen:
                seen.add(key_name)
                uniq.append(key_name)
        names = uniq

    if not names:
        if d.is_dir():
            for f in sorted(d.glob("xai-*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
                st = upload_state.get(f.name) if isinstance(upload_state.get(f.name), dict) else {}
                if body.pending_only and bool(st.get("uploaded")):
                    continue
                names.append(f.name)
    elif body.pending_only:
        filtered: list[str] = []
        for n in names:
            st = upload_state.get(n) if isinstance(upload_state.get(n), dict) else {}
            if not bool(st.get("uploaded")):
                filtered.append(n)
        names = filtered

    truncated = False
    limit = int(body.limit) if body.limit else 0
    if limit > 0 and len(names) > limit:
        names = names[:limit]
        truncated = True

    if not names:
        return {"ok": True, "total": 0, "success": 0, "failed": 0, "results": [], "message": "没有可上传的文件"}

    try:
        from cpa_export import upload_cpa_auth_file, mark_cpa_uploaded
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"无法加载 cpa_export: {e}") from e

    def _upload_one(name: str) -> dict:
        safe = Path(name).name
        if not safe.startswith("xai-") or not safe.endswith(".json") or ".." in safe:
            return {"file": safe, "ok": False, "error": "非法文件名"}
        path_file = d / safe
        if not path_file.is_file():
            return {"file": safe, "ok": False, "error": "文件不存在"}
        st = upload_state.get(safe) if isinstance(upload_state.get(safe), dict) else {}
        if not body.force and bool(st.get("uploaded")):
            return {"file": safe, "ok": True, "skipped": True, "detail": "already uploaded"}
        try:
            info = upload_cpa_auth_file(path_file, cfg, log_callback=lambda m: None)
            return {"file": safe, "ok": True, "status": info.get("status"), "response": info.get("response")}
        except Exception as e:
            try:
                mark_cpa_uploaded(path_file, ok=False, detail=str(e), auth_dir=d)
            except Exception:
                pass
            return {"file": safe, "ok": False, "error": str(e)[:400]}

    workers = max(1, min(int(body.workers or 4), 16, len(names)))
    results: list[dict] = []
    if len(names) == 1 or workers == 1:
        results = [_upload_one(n) for n in names]
    else:
        ordered: list[dict | None] = [None] * len(names)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            fut_map = {pool.submit(_upload_one, name): idx for idx, name in enumerate(names)}
            for fut in as_completed(fut_map):
                idx = fut_map[fut]
                try:
                    ordered[idx] = fut.result()
                except Exception as e:
                    ordered[idx] = {"file": names[idx], "ok": False, "error": str(e)[:400]}
        results = [
            r if isinstance(r, dict) else {"file": names[i], "ok": False, "error": "unknown"}
            for i, r in enumerate(ordered)
        ]

    success = sum(1 for r in results if r.get("ok"))
    failed = len(results) - success
    return {
        "ok": failed == 0,
        "total": len(names),
        "success": success,
        "failed": failed,
        "workers": workers,
        "truncated": truncated,
        "results": results,
    }


class CpaMarkBody(BaseModel):
    file: str | None = None
    files: list[str] | None = None
    uploaded: bool = True


@app.post("/api/cpa/mark")
def api_cpa_mark(request: Request, body: CpaMarkBody):
    """Manually mark local CPA files as uploaded/pending without calling CPAMC."""
    require_auth(request)
    d = resolve_cpa_dir()
    names: list[str] = []
    if body.file:
        names.append(str(body.file).strip())
    if body.files:
        names.extend(str(x).strip() for x in body.files if str(x).strip())
    if not names:
        raise HTTPException(status_code=400, detail="请提供 file 或 files")
    try:
        from cpa_export import mark_cpa_uploaded
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"无法加载 cpa_export: {e}") from e

    results = []
    for name in names:
        safe = Path(name).name
        if not safe.startswith("xai-") or not safe.endswith(".json"):
            results.append({"file": safe, "ok": False, "error": "非法文件名"})
            continue
        path_file = d / safe
        if not path_file.is_file():
            results.append({"file": safe, "ok": False, "error": "文件不存在"})
            continue
        mark_cpa_uploaded(
            path_file,
            ok=bool(body.uploaded),
            detail="manual mark" if body.uploaded else "manual unmark",
            auth_dir=d,
        )
        results.append({"file": safe, "ok": True, "uploaded": bool(body.uploaded)})
    return {"ok": True, "results": results}

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
        "moemail_api_key",
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
        "moemail_api_key",
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
    cmd_payload = None
    for k, v in (body.config or {}).items():
        if str(k).startswith("//"):
            continue
        # control channel piggyback (not persisted)
        if k in ("_cmd", "__cmd", "_ui_cmd", "__job"):
            cmd_payload = v
            continue
        if k in secret_keys and isinstance(v, str) and "*" in v and k in existing:
            if mask_secret(str(existing.get(k, ""))) == v:
                continue
        merged[k] = v
    # never persist control keys from old files either
    for k in ("_cmd", "__cmd", "_ui_cmd", "__job"):
        merged.pop(k, None)

    merged = _coerce_ui_bools(merged)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    app_cfg = APP_HOME / "config.json"
    try:
        if app_cfg.exists() or app_cfg.is_symlink():
            if not app_cfg.is_symlink():
                app_cfg.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            try:
                if app_cfg.exists():
                    app_cfg.unlink()
                app_cfg.symlink_to(path)
            except Exception:
                app_cfg.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass

    out: dict[str, Any] = {"ok": True, "path": str(path)}
    if cmd_payload is not None:
        if isinstance(cmd_payload, str):
            action = cmd_payload
            payload: dict[str, Any] = {}
        elif isinstance(cmd_payload, dict):
            action = str(cmd_payload.get("action") or cmd_payload.get("op") or "start")
            payload = dict(cmd_payload)
        else:
            raise HTTPException(400, "invalid _cmd payload")
        job_result = enqueue_job_request(action, payload)
        out["job_result"] = job_result
        out["job"] = job_result.get("job")
        out["cmd"] = action
    return out


def _start_register_job(body: RegisterBody) -> dict:
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
    # kind uses "run" so UI/logs avoid adblock-sensitive words
    jobs.start("run", cmd)
    return {"ok": True, "job": jobs.status()}


class TaskBody(BaseModel):
    action: str = "start"
    extra: int = Field(1, ge=1, le=500)
    threads: int = Field(1, ge=1, le=10)
    mint_workers: int = Field(-1, ge=-1, le=10)
    fast: bool = True
    inline_mint: bool = False
    limit: int = Field(1, ge=0, le=5000)
    email: str = ""
    timeout: int = Field(300, ge=30, le=3600)
    probe: bool = True
    headless: bool = False
    sleep: float = Field(3.0, ge=0, le=120)


@app.put("/api/task")
@app.post("/api/task")
def api_task(body: TaskBody, request: Request):
    """Unified job control. PUT preferred: some networks drop POST /api/jobs/*."""
    require_auth(request)
    payload = body.model_dump()
    action = payload.pop("action", "start")
    return enqueue_job_request(action, payload)


@app.post("/api/jobs/start")
def api_job_start(body: RegisterBody, request: Request):

    """Start registration job. Prefer this path (avoids adblock on /register)."""
    require_auth(request)
    return _start_register_job(body)


@app.post("/api/jobs/run")
def api_job_run(body: RegisterBody, request: Request):
    require_auth(request)
    return _start_register_job(body)


@app.post("/api/jobs/register")
def api_job_register(body: RegisterBody, request: Request):
    # legacy alias; some browsers/extensions block URLs containing "register"
    require_auth(request)
    return _start_register_job(body)


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


@app.get("/api/go")
def api_go(
    request: Request,
    action: str = Query("start"),
    extra: int = Query(1, ge=1, le=500),
    threads: int = Query(1, ge=1, le=10),
    mint_workers: int = Query(-1, ge=-1, le=10),
    fast: int = Query(1, ge=0, le=1),
    limit: int = Query(1, ge=0, le=5000),
    email: str = Query(""),
    timeout: int = Query(300, ge=30, le=3600),
    probe: int = Query(1, ge=0, le=1),
    headless: int = Query(0, ge=0, le=1),
):
    """GET-based job control for networks that block POST/PUT job paths."""
    require_auth(request)
    payload: dict[str, Any] = {
        "extra": extra,
        "threads": threads,
        "mint_workers": mint_workers,
        "fast": bool(fast),
        "limit": limit,
        "email": email,
        "timeout": timeout,
        "probe": bool(probe),
        "headless": bool(headless),
    }
    return enqueue_job_request(action, payload)


@app.get("/api/status/do")
def api_status_do(
    request: Request,
    action: str = Query("start"),
    extra: int = Query(1, ge=1, le=500),
    threads: int = Query(1, ge=1, le=10),
    mint_workers: int = Query(-1, ge=-1, le=10),
    fast: int = Query(1, ge=0, le=1),
):
    """Alias under /api/status/* — often allowed when other paths are filtered."""
    require_auth(request)
    return enqueue_job_request(
        action,
        {
            "extra": extra,
            "threads": threads,
            "mint_workers": mint_workers,
            "fast": bool(fast),
        },
    )



class ArchiveAccountsBody(BaseModel):
    run_backfill: bool = True
    backfill_timeout: int = Field(3600, ge=60, le=14400)


@app.post("/api/archive/accounts")
def api_archive_accounts(request: Request, body: ArchiveAccountsBody | None = None):
    """Archive accounts_cli.txt after optional missing-CPA backfill."""
    require_auth(request)
    body = body or ArchiveAccountsBody()
    if jobs.is_running():
        raise HTTPException(409, "有任务正在运行，请先停止后再归档")
    try:
        result = archive_accounts_file(
            run_backfill=bool(body.run_backfill),
            backfill_timeout=int(body.backfill_timeout or 3600),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"账号归档失败: {e}") from e
    jobs.append_log(f"[archive] accounts -> {result.get('backup_file') or result.get('message')}")
    return result


@app.post("/api/archive/cpa")
def api_archive_cpa(request: Request):
    """Move current CPA auth files into cpa_file_backup/<dated>/."""
    require_auth(request)
    if jobs.is_running():
        raise HTTPException(409, "有任务正在运行，请先停止后再归档")
    try:
        result = archive_cpa_files()
    except Exception as e:
        raise HTTPException(500, f"CPA 归档失败: {e}") from e
    jobs.append_log(f"[archive] cpa moved={result.get('moved_count')} batch={result.get('batch_name')}")
    return result


@app.get("/api/archive/status")
def api_archive_status(request: Request):
    require_auth(request)
    ab = resolve_accounts_backup_dir()
    cb = resolve_cpa_backup_dir()
    a_files = sorted(ab.glob("accounts_cli_*.txt"), key=lambda p: p.stat().st_mtime, reverse=True) if ab.is_dir() else []
    c_batches = sorted([p for p in cb.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True) if cb.is_dir() else []
    c_loose = list(cb.glob("xai-*.json")) if cb.is_dir() else []
    return {
        "accounts_backup_dir": str(ab),
        "cpa_backup_dir": str(cb),
        "accounts_archives": len(a_files),
        "cpa_batches": len(c_batches),
        "cpa_loose_files": len(c_loose),
        "latest_accounts": a_files[0].name if a_files else "",
        "latest_cpa_batch": c_batches[0].name if c_batches else "",
    }


@app.get("/api/download/accounts-archive")
def download_accounts_archive(request: Request):
    """Download all archived accounts_cli_*.txt as zip."""
    require_auth(request)
    d = resolve_accounts_backup_dir()
    files = sorted(d.glob("accounts_cli_*.txt")) if d.is_dir() else []
    if not files:
        raise HTTPException(404, "暂无归档账号文件")
    data = _zip_directory(d)
    ts = _stamp()
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="accounts_archive_{ts}.zip"',
            "Content-Length": str(len(data)),
        },
    )


@app.get("/api/download/cpa-archive")
def download_cpa_archive(request: Request):
    """Download archived CPA files (all batches under cpa_file_backup) as zip."""
    require_auth(request)
    d = resolve_cpa_backup_dir()
    if not d.is_dir():
        raise HTTPException(404, "暂无归档 CPA 文件")
    has = any(d.rglob("xai-*.json")) or any(d.glob("xai-*.json"))
    if not has:
        # still allow zip of whatever is there
        has = any(p.is_file() for p in d.rglob("*"))
    if not has:
        raise HTTPException(404, "暂无归档 CPA 文件")
    data = _zip_directory(d)
    ts = _stamp()
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="cpa_archive_{ts}.zip"',
            "Content-Length": str(len(data)),
        },
    )

@app.get("/api/download/accounts")
def download_accounts(request: Request):
    require_auth(request)
    path = resolve_accounts_path()
    if not path.is_file():
        raise HTTPException(404, "accounts file not found")
    return FileResponse(path, filename="accounts_cli.txt", media_type="text/plain")


@app.get("/api/download/cpa")
def download_cpa_zip(request: Request):
    """Download all xai-*.json as a zip archive."""
    require_auth(request)
    d = resolve_cpa_dir()
    files = sorted(d.glob("xai-*.json")) if d.is_dir() else []
    if not files:
        raise HTTPException(404, "no CPA files (xai-*.json) found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            # only pack credential files, skip summary/failed logs
            zf.write(f, arcname=f.name)
    data = buf.getvalue()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"cpa_auths_{ts}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(data)),
        },
    )


@app.get("/api/download/cpa/{filename}")
def download_cpa_one(request: Request, filename: str):
    """Download a single xai-*.json file."""
    require_auth(request)
    name = Path(filename).name
    if not name.startswith("xai-") or not name.endswith(".json"):
        raise HTTPException(400, "only xai-*.json allowed")
    if ".." in name or "/" in name or "\\" in name:
        raise HTTPException(400, "invalid filename")
    path = resolve_cpa_dir() / name
    if not path.is_file():
        raise HTTPException(404, "CPA file not found")
    return FileResponse(path, filename=name, media_type="application/json")


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
