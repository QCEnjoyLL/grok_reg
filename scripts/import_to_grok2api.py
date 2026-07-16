#!/usr/bin/env python3
"""CLI job: import accounts SSO/CPA into Grok2API (used by dashboard)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from grok2api_import import import_from_config  # noqa: E402


def load_cfg(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        return {
            k: v
            for k, v in raw.items()
            if not (isinstance(k, str) and (k.startswith("//") or k.startswith("#")))
        }
    except Exception as e:
        print(f"warn: config: {e}", flush=True)
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(_ROOT / "config.json"))
    ap.add_argument("--accounts", default=str(_ROOT / "accounts_cli.txt"))
    ap.add_argument("--cpa-dir", default="")
    ap.add_argument("--mode", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--emails-file", default="")
    args = ap.parse_args()

    cfg = load_cfg(Path(args.config))
    emails = []
    if args.emails_file:
        p = Path(args.emails_file)
        if p.is_file():
            emails = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def log(m: str) -> None:
        print(m, flush=True)

    print(
        f"[g2a] start accounts={args.accounts} mode={args.mode or '(auto)'} "
        f"limit={args.limit} emails={len(emails)}",
        flush=True,
    )
    result = import_from_config(
        cfg,
        accounts_file=args.accounts,
        cpa_dir=args.cpa_dir or None,
        emails=emails or None,
        limit=args.limit,
        mode=args.mode,
        log=log,
    )
    print(json.dumps({k: v for k, v in result.items() if k != "response"}, ensure_ascii=False, indent=2), flush=True)
    if result.get("ok"):
        print(
            f"=== Grok2API 导入完成 mode={result.get('mode')} imported={result.get('imported') or result.get('files') or 0} ===",
            flush=True,
        )
        return 0
    print(f"=== Grok2API 导入失败: {result.get('error') or result} ===", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
