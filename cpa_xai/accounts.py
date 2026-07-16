"""Parse register machine accounts_cli.txt lines."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class AccountLine:
    email: str
    password: str
    sso: str
    raw: str
    line_no: int


def parse_accounts_file(path: str | Path) -> list[AccountLine]:
    path = Path(path)
    out: list[AccountLine] = []
    if not path.is_file():
        return out
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split("----")
        if len(parts) < 2:
            continue
        email = parts[0].strip()
        password = parts[1].strip()
        sso = parts[2].strip() if len(parts) > 2 else ""
        # If more than 3 segments (password itself contained ----), keep password=parts[1]
        # and join remaining as sso. Never put sso into password.
        if len(parts) > 3:
            password = parts[1].strip()
            sso = "----".join(p.strip() for p in parts[2:]).strip()
        # Guard: absurdly long "password" is almost certainly mis-parsed cookie/token
        if len(password) > 80:
            # common footgun: email----sso (missing password) or email----sso----...
            # If middle looks like jwt/base64 cookie, treat as sso and leave password empty skip
            maybe_token = password
            if maybe_token.count(".") >= 2 or len(maybe_token) > 120:
                # cannot mint browser without real password; keep as-is but mark via empty if sso-only line
                if not sso and len(parts) == 2:
                    sso = password
                    password = ""
        if not email or not password:
            continue
        out.append(AccountLine(email=email, password=password, sso=sso, raw=s, line_no=i))
    return out


def existing_cpa_emails(auth_dir: str | Path) -> set[str]:
    """Emails already present as xai-*.json in auth_dir."""
    auth_dir = Path(auth_dir)
    found: set[str] = set()
    if not auth_dir.is_dir():
        return found
    for p in auth_dir.glob("xai-*.json"):
        name = p.name[len("xai-") : -len(".json")]
        if name:
            found.add(name.lower())
        try:
            import json

            d = json.loads(p.read_text(encoding="utf-8"))
            em = str(d.get("email") or "").strip().lower()
            if em:
                found.add(em)
        except Exception:
            continue
    return found


def remove_accounts_by_email(path: str | Path, emails: list[str] | set[str]) -> dict:
    """Remove account lines whose email matches (case-insensitive)."""
    path = Path(path)
    wanted = {str(e or "").strip().lower() for e in emails if str(e or "").strip()}
    wanted.discard("")
    if not wanted:
        return {"removed_count": 0, "removed_emails": [], "remaining": 0, "path": str(path)}
    if not path.is_file():
        return {
            "removed_count": 0,
            "removed_emails": [],
            "remaining": 0,
            "path": str(path),
            "missing_file": True,
        }

    kept: list[str] = []
    removed: list[str] = []
    for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith("#"):
            kept.append(s)
            continue
        parts = s.split("----")
        email = (parts[0] if parts else s).strip()
        if email.lower() in wanted:
            removed.append(email)
            continue
        kept.append(s)

    text = "\n".join(kept)
    if kept:
        text += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return {
        "removed_count": len(removed),
        "removed_emails": removed[:100],
        "remaining": len(kept),
        "path": str(path),
    }
