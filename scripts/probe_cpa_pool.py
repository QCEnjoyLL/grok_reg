#!/usr/bin/env python3
"""Batch probe CPA xai-*.json usability with progress + cooperative cancel.

Borrowed UX from grok-register-lite: stoppable probe job with progress lines.
Does NOT change registration flow. Soft fails keep files; hard 401/403 may delete
when --delete and config/cpa_delete_unusable allow.

Example:
  python -u scripts/probe_cpa_pool.py --limit 0 --workers 4 --delete
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cpa_export import purge_unusable_cpa_account, resolve_cpa_proxy  # noqa: E402
from cpa_xai.mint import access_token_from_cpa_file  # noqa: E402
from cpa_xai.probe import probe_usability  # noqa: E402

_stop = threading.Event()


def _on_signal(signum, frame) -> None:  # noqa: ANN001, ARG001
    _stop.set()
    print("[probe] stop requested", flush=True)


def load_cfg(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        return {
            k: v
            for k, v in raw.items()
            if not (isinstance(k, str) and (k.startswith("//") or k.startswith("#")))
        }
    except Exception as e:  # noqa: BLE001
        print(f"warn: config read failed: {e}", flush=True)
        return {}


def list_cpa_files(auth_dir: Path, email: str = "") -> list[Path]:
    if not auth_dir.is_dir():
        return []
    email = (email or "").strip().lower()
    files = sorted(auth_dir.glob("xai-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not email:
        return files
    out: list[Path] = []
    for f in files:
        name = f.name[len("xai-") : -len(".json")].lower()
        if email in name or name == email:
            out.append(f)
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            em = str(d.get("email") or "").strip().lower()
            if em == email or email in em:
                out.append(f)
        except Exception:
            continue
    return out


def email_from_file(path: Path) -> str:
    name = path.name
    if name.startswith("xai-") and name.endswith(".json"):
        return name[len("xai-") : -len(".json")]
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        em = str(d.get("email") or "").strip()
        if em:
            return em
    except Exception:
        pass
    return name


def probe_one(
    path: Path,
    *,
    cfg: dict[str, Any],
    base_url: str,
    proxy: str | None,
    do_delete: bool,
) -> dict[str, Any]:
    email = email_from_file(path)
    row: dict[str, Any] = {
        "file": path.name,
        "email": email,
        "ok": False,
        "usable": False,
        "unusable": False,
        "deleted": False,
        "reason": "",
        "status": 0,
    }
    if _stop.is_set():
        row["reason"] = "cancelled"
        return row
    try:
        access = access_token_from_cpa_file(path)
        if not access:
            row["reason"] = "no access_token"
            row["unusable"] = True
            return row
        u = probe_usability(access, base_url=base_url, proxy=proxy)
        row["usable"] = bool(u.get("usable"))
        row["unusable"] = bool(u.get("unusable"))
        row["reason"] = str(u.get("reason") or "")
        row["status"] = u.get("status") or 0
        row["ok"] = True
        if row["unusable"] and do_delete:
            purged = purge_unusable_cpa_account(
                email=email,
                cfg=cfg,
                auth_dir=path.parent,
                cpa_path=path,
                log_callback=lambda m: print(m, flush=True),
            )
            row["deleted"] = True
            row["purged"] = purged
    except Exception as e:  # noqa: BLE001
        row["reason"] = f"error: {e}"
    return row


def main() -> int:
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _on_signal)
        except Exception:
            pass

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default="", help="CPA dir (default config cpa_auth_dir or ./cpa_auths)")
    ap.add_argument("--config", default=str(_ROOT / "config.json"))
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--email", default="", help="Filter single email / substring")
    ap.add_argument("--workers", type=int, default=4, help="Concurrent probes")
    ap.add_argument("--delete", action="store_true", help="Force delete hard-unusable (401/403)")
    ap.add_argument("--no-delete", action="store_true", help="Never delete")
    ap.add_argument("--proxy", default="", help="Override proxy; empty = config")
    ap.add_argument("--base-url", default="", help="Probe base URL (default config cpa_base_url)")
    ap.add_argument("--sleep", type=float, default=0.0, help="Optional delay between submissions")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.is_file():
        for cand in (
            Path(os.environ.get("DATA_DIR") or "") / "config.json",
            _ROOT / "data" / "config.json",
            _ROOT / "config.json",
        ):
            if cand.is_file():
                cfg_path = cand
                break
    cfg = load_cfg(str(cfg_path))

    if args.out_dir:
        auth_dir = Path(args.out_dir).expanduser()
    else:
        raw = str(cfg.get("cpa_auth_dir") or "").strip() or "./cpa_auths"
        auth_dir = Path(raw).expanduser()
        if not auth_dir.is_absolute():
            data = os.environ.get("DATA_DIR") or os.environ.get("GROK_REG_DATA_DIR")
            if data:
                auth_dir = Path(data) / str(raw).lstrip("./")
            else:
                auth_dir = (_ROOT / auth_dir).resolve()
    try:
        auth_dir = auth_dir.resolve()
    except Exception:
        pass

    base_url = (args.base_url or str(cfg.get("cpa_base_url") or "https://cli-chat-proxy.grok.com/v1")).strip()
    if args.proxy.strip():
        proxy = args.proxy.strip()
        if proxy.lower() in {"direct", "none", "off", "-"}:
            proxy = ""
    else:
        proxy = resolve_cpa_proxy(cfg) or ""

    if args.no_delete:
        do_delete = False
    elif args.delete:
        do_delete = True
    else:
        do_delete = bool(cfg.get("cpa_delete_unusable", True))

    files = list_cpa_files(auth_dir, args.email)
    if args.offset:
        files = files[args.offset :]
    if args.limit and args.limit > 0:
        files = files[: args.limit]

    total = len(files)
    print(
        f"[probe] dir={auth_dir} total={total} workers={max(1, args.workers)} "
        f"delete={do_delete} base={base_url} proxy={'yes' if proxy else 'no'} config={cfg_path}",
        flush=True,
    )
    if total == 0:
        print("[probe] nothing to do", flush=True)
        return 0

    stats = {"done": 0, "usable": 0, "unusable": 0, "soft_fail": 0, "deleted": 0, "error": 0}
    results: list[dict[str, Any]] = []
    lock = threading.Lock()
    workers = max(1, min(32, int(args.workers or 1)))

    def _job(p: Path) -> dict[str, Any]:
        if _stop.is_set():
            return {"file": p.name, "email": email_from_file(p), "reason": "cancelled", "ok": False}
        if args.sleep > 0:
            time.sleep(args.sleep)
        return probe_one(p, cfg=cfg, base_url=base_url, proxy=proxy or None, do_delete=do_delete)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_job, p): p for p in files}
        for fut in as_completed(futs):
            if _stop.is_set():
                break
            try:
                row = fut.result()
            except Exception as e:  # noqa: BLE001
                p = futs[fut]
                row = {
                    "file": p.name,
                    "email": email_from_file(p),
                    "ok": False,
                    "reason": f"worker error: {e}",
                }
            with lock:
                results.append(row)
                stats["done"] += 1
                if row.get("deleted"):
                    stats["deleted"] += 1
                if row.get("usable"):
                    stats["usable"] += 1
                    tag = "OK"
                elif row.get("unusable"):
                    stats["unusable"] += 1
                    tag = "UNUSABLE"
                elif not row.get("ok"):
                    stats["error"] += 1
                    tag = "ERR"
                else:
                    stats["soft_fail"] += 1
                    tag = "SOFT"
                print(
                    f"[probe] [{stats['done']}/{total}] {tag} {row.get('email') or row.get('file')} "
                    f"status={row.get('status')} reason={str(row.get('reason') or '')[:120]}"
                    + (" deleted" if row.get("deleted") else ""),
                    flush=True,
                )
                print(
                    f"[probe] progress done={stats['done']} total={total} "
                    f"usable={stats['usable']} unusable={stats['unusable']} "
                    f"soft={stats['soft_fail']} deleted={stats['deleted']} err={stats['error']}",
                    flush=True,
                )

    if _stop.is_set():
        print("[probe] cancelled", flush=True)

    summary = {
        "dir": str(auth_dir),
        "total": total,
        "stats": stats,
        "stopped": _stop.is_set(),
        "delete": do_delete,
        "ts": int(time.time()),
        "results": results[:5000],
    }
    try:
        auth_dir.mkdir(parents=True, exist_ok=True)
        out = auth_dir / f"probe_summary_{int(time.time())}.json"
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[probe] summary {out}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[probe] summary write failed: {e}", flush=True)

    print(
        f"=== done usable={stats['usable']} unusable={stats['unusable']} "
        f"soft={stats['soft_fail']} deleted={stats['deleted']} err={stats['error']} "
        f"done={stats['done']}/{total} ===",
        flush=True,
    )
    return 0 if not _stop.is_set() else 130


if __name__ == "__main__":
    raise SystemExit(main())
