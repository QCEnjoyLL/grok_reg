"""Grok Register web dashboard - job control, logs, accounts, config, noVNC link."""
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
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

APP_HOME = Path(os.environ.get("APP_HOME", Path(__file__).resolve().parents[1]))
DATA_DIR = Path(os.environ.get("DATA_DIR", APP_HOME / "data"))
WEB_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))

# Ensure project root importable
if str(APP_HOME) not in sys.path:
    sys.path.insert(0, str(APP_HOME))

MAX_LOG_LINES = 4000
WEB_TOKEN = os.environ.get("WEB_TOKEN", "").strip()
DASHBOARD_TITLE = os.environ.get("DASHBOARD_TITLE", "Grok Register")


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def resolve_config_path() -> Path:
    for p in (DATA_DIR / "config.json", APP_HOME / "config.json", APP_HOME / "config.example.json"):
        if p.is_file():
            return p
    return APP_HOME / "config.json"


def resolve_accounts_path() -> Path:
    data_p = DATA_DIR / "accounts_cli.txt"
    app_p = APP_HOME / "accounts_cli.txt"
    if data_p.is_file() or DATA_DIR.is_dir():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return data_p
    return app_p


def resolve_cpa_dir() -> Path:
    cfg = load_config_dict()
    raw = str(cfg.get("cpa_auth_dir") or "./cpa_auths")
    p = Path(raw).expanduser()
    if p.is_absolute():
        p.mkdir(parents=True, exist_ok=True)
        return p
    # relative paths always land on the data volume in Docker
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
    # strip comment keys
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
        self._readers: list[threading.Thread] = []
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
        # best-effort from register_cli summary / progress
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

            t = threading.Thread(target=_reader, daemon=True, name="job-log-reader")
            t.start()
            self._readers = [t]

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
app = FastAPI(title=DASHBOARD_TITLE, version="1.0.0")
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


def _check_token(request: Request) -> None:
    if not WEB_TOKEN:
        return
    auth = request.headers.get("Authorization", "")
    q = request.query_params.get("token", "")
    cookie = request.cookies.get("web_token", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    got = bearer or q or cookie or request.headers.get("X-Web-Token", "")
    if got != WEB_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized: set WEB_TOKEN header/query")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # allow health without token
    if request.url.path in ("/api/health", "/healthz"):
        return await call_next(request)
    if request.url.path.startswith("/static"):
        return await call_next(request)
    try:
        if WEB_TOKEN and request.url.path.startswith("/api"):
            _check_token(request)
        elif WEB_TOKEN and request.url.path == "/":
            # page itself checks via query cookie set by frontend
            pass
    except HTTPException as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status_code)
    return await call_next(request)


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


@app.get("/healthz")
@app.get("/api/health")
def health():
    return {
        "ok": True,
        "time": _now_iso(),
        "display": os.environ.get("DISPLAY", ""),
        "data_dir": str(DATA_DIR),
        "job_running": jobs.is_running(),
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {
            "title": DASHBOARD_TITLE,
            "need_token": bool(WEB_TOKEN),
            "novnc_port": os.environ.get("NOVNC_PORT", "6080"),
            "web_port": os.environ.get("WEB_PORT", "8080"),
        },
    )


@app.get("/api/status")
def api_status(request: Request):
    _check_token(request)
    accounts = resolve_accounts_path()
    cpa_dir = resolve_cpa_dir()
    account_count = 0
    if accounts.is_file():
        account_count = sum(1 for line in accounts.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip())
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
        "novnc_path": f":{os.environ.get('NOVNC_PORT', '6080')}/vnc.html",
    }


@app.get("/api/logs")
def api_logs(request: Request, tail: int = Query(200, ge=1, le=4000)):
    _check_token(request)
    return {"lines": jobs.get_logs(tail)}


@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket, token: str = ""):
    if WEB_TOKEN and token != WEB_TOKEN and ws.headers.get("x-web-token") != WEB_TOKEN:
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
    _check_token(request)
    path = resolve_accounts_path()
    rows = []
    if path.is_file():
        lines = [ln.strip() for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
        slice_ = lines[offset : offset + limit]
        for ln in slice_:
            parts = ln.split("----")
            email = parts[0] if parts else ln
            password = parts[1] if len(parts) > 1 else ""
            sso = parts[2] if len(parts) > 2 else ""
            rows.append(
                {
                    "email": email,
                    "password": password,
                    "sso_preview": (sso[:24] + "…") if len(sso) > 24 else sso,
                    "has_sso": bool(sso),
                }
            )
    total = 0
    if path.is_file():
        total = sum(1 for line in path.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip())
    return {"total": total, "items": rows}


@app.get("/api/cpa")
def api_cpa(request: Request, limit: int = Query(100, ge=1, le=2000)):
    _check_token(request)
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
    _check_token(request)
    path = resolve_config_path()
    if not path.is_file():
        return {"path": str(path), "config": {}}
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
    return {"path": str(path), "config": out, "redacted": redact}


@app.put("/api/config")
def api_config_put(body: ConfigUpdateBody, request: Request):
    _check_token(request)
    path = resolve_config_path()
    # write to data dir if possible
    if path == APP_HOME / "config.example.json" or str(path).endswith("config.example.json"):
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
        # skip unchanged redacted placeholders
        if k in secret_keys and isinstance(v, str) and "*" in v and k in existing:
            if mask_secret(str(existing.get(k, ""))) == v:
                continue
        merged[k] = v
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # keep app symlink/copy in sync
    app_cfg = APP_HOME / "config.json"
    try:
        if app_cfg.is_symlink() or app_cfg.exists():
            if app_cfg.resolve() != path.resolve():
                app_cfg.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            app_cfg.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass
    return {"ok": True, "path": str(path)}


@app.post("/api/jobs/register")
def api_job_register(body: RegisterBody, request: Request):
    _check_token(request)
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
    _check_token(request)
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
    _check_token(request)
    stopped = jobs.stop()
    return {"ok": True, "stopped": stopped, "job": jobs.status()}


@app.get("/api/download/accounts")
def download_accounts(request: Request):
    _check_token(request)
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
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
