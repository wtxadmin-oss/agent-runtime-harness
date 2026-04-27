"""Local profile metadata store for webui session isolation."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from nanobot.config.loader import load_config
from nanobot.utils.helpers import ensure_dir
from nanobot.utils.helpers import sync_workspace_templates

DEFAULT_PROFILE_ID = "demo_alice"
_SLUG_RE = re.compile(r"[^a-z0-9_-]+")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class Profile:
    id: str
    name: str


def _workspace_root() -> Path:
    """Resolve current workspace path from active config."""
    try:
        return ensure_dir(load_config().workspace_path)
    except Exception as e:
        logger.warning("profile store fallback workspace path: {}", e)
        return ensure_dir(Path.home() / ".nanobot" / "workspace")


def _profiles_path() -> Path:
    return ensure_dir(_workspace_root() / "users") / "profiles.json"


def _default_data() -> dict:
    return {
        "default_profile": "",
        "profiles": [],
    }


def _sanitize_profiles(raw: object) -> dict:
    """Normalize on-disk JSON profile records."""
    data = raw if isinstance(raw, dict) else {}
    default_profile = data.get("default_profile")
    if not isinstance(default_profile, str):
        default_profile = ""
    default_profile = default_profile.strip()
    profiles_raw = data.get("profiles")
    rows = profiles_raw if isinstance(profiles_raw, list) else []
    cleaned: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = row.get("id")
        name = row.get("name")
        if not isinstance(pid, str) or not isinstance(name, str):
            continue
        pid = pid.strip()
        name = name.strip()
        if not pid or not name or pid in seen:
            continue
        cleaned.append({"id": pid, "name": name})
        seen.add(pid)

    if default_profile not in seen:
        default_profile = cleaned[0]["id"] if cleaned else ""
    return {"default_profile": default_profile, "profiles": cleaned}


def _read_data() -> dict:
    path = _profiles_path()
    if not path.exists():
        data = _default_data()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("profile store: invalid profiles.json, resetting to defaults")
        raw = _default_data()
    data = _sanitize_profiles(raw)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def _write_data(data: dict) -> None:
    path = _profiles_path()
    clean = _sanitize_profiles(data)
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")


def _slugify(name: str) -> str:
    text = _WS_RE.sub("-", name.strip().lower())
    text = _SLUG_RE.sub("-", text)
    return text.strip("-_")


def _bootstrap_profile_workspace(profile_id: str) -> None:
    profile_ws = ensure_dir(_workspace_root() / "users" / profile_id)
    sync_workspace_templates(profile_ws, silent=True)


def list_profiles() -> list[dict[str, str]]:
    """Return available profiles as JSON-safe rows."""
    data = _read_data()
    return [dict(p) for p in data["profiles"]]


def create_profile(name: str) -> Profile:
    """Create and persist a new profile from display name."""
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("profile name cannot be empty")

    data = _read_data()
    existing = {p["id"] for p in data["profiles"] if isinstance(p.get("id"), str)}
    base = _slugify(clean_name) or "user"
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}-{suffix}"
        suffix += 1

    row = {"id": candidate, "name": clean_name}
    data["profiles"].append(row)
    if not data.get("default_profile"):
        data["default_profile"] = row["id"]
    _write_data(data)
    _bootstrap_profile_workspace(row["id"])
    return Profile(id=row["id"], name=row["name"])


def delete_profile(profile_id: str) -> bool:
    """Delete a profile and purge its scoped local data."""
    target = (profile_id or "").strip()
    if not target:
        return False

    data = _read_data()
    rows = data.get("profiles")
    if not isinstance(rows, list):
        return False

    keep: list[dict[str, str]] = []
    found = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = row.get("id")
        if not isinstance(pid, str):
            continue
        if pid == target:
            found = True
            continue
        keep.append(row)
    if not found:
        return False

    root = _workspace_root()
    user_dir = root / "users" / target
    try:
        if user_dir.exists():
            shutil.rmtree(user_dir)
    except OSError:
        logger.warning("profile store: failed to remove user dir for {}", target)
        return False

    sessions_dir = root / "sessions"
    if sessions_dir.is_dir():
        for path in sessions_dir.glob(f"websocket_{target}_*.jsonl"):
            try:
                path.unlink()
            except OSError:
                logger.warning("profile store: failed to remove session file {}", path)
                return False

    data["profiles"] = keep
    if data.get("default_profile") == target:
        data["default_profile"] = keep[0]["id"] if keep else ""
    _write_data(data)
    return True


def normalize_profile_id(value: str | None) -> str:
    """Return a known profile id, falling back to default profile."""
    data = _read_data()
    rows = data["profiles"] if isinstance(data.get("profiles"), list) else []
    ordered_ids = [p["id"] for p in rows if isinstance(p, dict) and isinstance(p.get("id"), str)]
    valid = set(ordered_ids)
    if value:
        text = value.strip()
        if text in valid:
            return text
    default_profile = data.get("default_profile")
    if isinstance(default_profile, str):
        default_profile = default_profile.strip()
        if default_profile in valid:
            return default_profile
    return ordered_ids[0] if ordered_ids else ""
