"""Profile-scoped model storage for WebUI dynamic model switching."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.config.loader import load_config
from nanobot.config.loader import resolve_config_env_vars
from nanobot.utils.helpers import ensure_dir

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ENV_SEGMENT_RE = re.compile(r"[^A-Za-z0-9_]+")
GLOBAL_BASE_MODEL_ID = "global:base"
GLOBAL_BASE_MODEL_NAME = "DeepSeek-V4-Flash"


def _workspace_root() -> Path:
    """Resolve current workspace path from active config."""
    try:
        return ensure_dir(load_config().workspace_path)
    except Exception as e:
        logger.warning("model store fallback workspace path: {}", e)
        return ensure_dir(Path.home() / ".nanobot" / "workspace")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug_env(text: str) -> str:
    return _ENV_SEGMENT_RE.sub("_", text.strip().upper()).strip("_")


def _strip_wrapped_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        inner = value[1:-1]
        if value[0] == '"':
            return bytes(inner, "utf-8").decode("unicode_escape")
        return inner
    return value


def _encode_env_value(value: str) -> str:
    if value == "":
        return '""'
    needs_quote = any(ch.isspace() for ch in value) or any(ch in value for ch in '#"\\')
    if not needs_quote:
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


class ModelStore:
    """Store user-defined model endpoints per profile.

    Non-sensitive metadata is written to ``models.json``. API keys are stored
    in profile-local ``.env.models`` as env vars referenced by ``api_key_env``.
    """

    def __init__(self, workspace_root: Path | None = None) -> None:
        self._workspace_root = ensure_dir(workspace_root or _workspace_root())

    def _profile_root(self, profile_id: str | None) -> Path:
        pid = (profile_id or "").strip()
        if not pid:
            raise ValueError("profile_id is required")
        return ensure_dir(self._workspace_root / "users" / pid)

    @staticmethod
    def _models_filename() -> str:
        return "models.json"

    @staticmethod
    def _env_filename() -> str:
        return ".env.models"

    def _models_path(self, profile_id: str | None) -> Path:
        return self._profile_root(profile_id) / self._models_filename()

    def _env_path(self, profile_id: str | None) -> Path:
        return self._profile_root(profile_id) / self._env_filename()

    @staticmethod
    def _default_payload() -> dict[str, Any]:
        return {"models": []}

    @staticmethod
    def _sanitize_row(raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        model_id = raw.get("id")
        name = raw.get("name")
        model_name = raw.get("model_name")
        base_url = raw.get("base_url")
        api_key_env = raw.get("api_key_env")
        supports_thinking = raw.get("supports_thinking", False)
        created_at = raw.get("created_at")
        updated_at = raw.get("updated_at")

        if not all(isinstance(v, str) and v.strip() for v in (
            model_id, name, model_name, base_url, api_key_env,
        )):
            return None
        if not isinstance(supports_thinking, bool):
            supports_thinking = bool(supports_thinking)

        cleaned: dict[str, Any] = {
            "id": model_id.strip(),
            "name": name.strip(),
            "model_name": model_name.strip(),
            "base_url": base_url.strip(),
            "api_key_env": api_key_env.strip(),
            "supports_thinking": supports_thinking,
            "created_at": created_at if isinstance(created_at, str) and created_at else _utc_now_iso(),
            "updated_at": updated_at if isinstance(updated_at, str) and updated_at else _utc_now_iso(),
        }
        last_selected_at = raw.get("last_selected_at")
        if isinstance(last_selected_at, str) and last_selected_at:
            cleaned["last_selected_at"] = last_selected_at
        return cleaned

    def _read_models(self, profile_id: str | None) -> list[dict[str, Any]]:
        path = self._models_path(profile_id)
        if not path.exists():
            payload = self._default_payload()
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("model store: invalid models.json, resetting")
            self._write_models(profile_id, [])
            return []

        rows_raw = raw.get("models") if isinstance(raw, dict) else None
        rows = rows_raw if isinstance(rows_raw, list) else []
        models: list[dict[str, Any]] = []
        seen: set[str] = set()
        needs_write = not isinstance(raw, dict) or not isinstance(rows_raw, list)
        for row in rows:
            cleaned = self._sanitize_row(row)
            if cleaned is None:
                needs_write = True
                continue
            model_id = cleaned["id"]
            if model_id in seen:
                needs_write = True
                continue
            if cleaned != row:
                needs_write = True
            models.append(cleaned)
            seen.add(model_id)
        if needs_write:
            self._write_models(profile_id, models)
        return models

    def _write_models(self, profile_id: str | None, rows: list[dict[str, Any]]) -> None:
        path = self._models_path(profile_id)
        payload = {"models": rows}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_env(self, profile_id: str | None) -> dict[str, str]:
        path = self._env_path(profile_id)
        if not path.exists():
            path.write_text("", encoding="utf-8")
            return {}

        out: dict[str, str] = {}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return out
        for line in lines:
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            if text.startswith("export "):
                text = text[len("export "):].strip()
            if "=" not in text:
                continue
            key, raw_val = text.split("=", 1)
            key = key.strip()
            if not _ENV_KEY_RE.match(key):
                continue
            out[key] = _strip_wrapped_quotes(raw_val.strip())
        return out

    def _write_env(self, profile_id: str | None, entries: dict[str, str]) -> None:
        path = self._env_path(profile_id)
        keys = sorted(entries.keys())
        lines = [f"{k}={_encode_env_value(entries[k])}" for k in keys]
        content = "\n".join(lines)
        if content:
            content += "\n"
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _public_row(row: dict[str, Any], *, has_api_key: bool) -> dict[str, Any]:
        out = dict(row)
        out["has_api_key"] = has_api_key
        return out

    def list_models(self, profile_id: str | None) -> list[dict[str, Any]]:
        rows = self._read_models(profile_id)
        env_map = self._read_env(profile_id)
        out: list[dict[str, Any]] = []
        for row in rows:
            env_key = row["api_key_env"]
            has_api_key = bool(env_map.get(env_key, "").strip())
            out.append(self._public_row(row, has_api_key=has_api_key))
        return out

    def get_model(self, profile_id: str | None, model_id: str) -> dict[str, Any] | None:
        target = (model_id or "").strip()
        if not target:
            return None
        rows = self._read_models(profile_id)
        row = next((r for r in rows if r["id"] == target), None)
        if row is None:
            return None
        env_map = self._read_env(profile_id)
        secret = env_map.get(row["api_key_env"])
        out = dict(row)
        out["api_key"] = secret
        out["has_api_key"] = bool((secret or "").strip())
        return out

    def add_model(
        self,
        profile_id: str | None,
        *,
        name: str,
        model_name: str,
        base_url: str,
        api_key: str,
        supports_thinking: bool,
    ) -> dict[str, Any]:
        clean_name = (name or "").strip()
        clean_model = (model_name or "").strip()
        clean_base = (base_url or "").strip()
        if not clean_name:
            raise ValueError("name is required")
        if not clean_model:
            raise ValueError("model_name is required")
        if not clean_base:
            raise ValueError("base_url is required")

        model_id = str(uuid.uuid4())
        now = _utc_now_iso()
        pid = (profile_id or "").strip()
        if not pid:
            raise ValueError("profile_id is required")
        env_key = f"NANOBOT_PROFILE_{_slug_env(pid)}_MODEL_{_slug_env(model_id)}_API_KEY"

        row = {
            "id": model_id,
            "name": clean_name,
            "model_name": clean_model,
            "base_url": clean_base,
            "api_key_env": env_key,
            "supports_thinking": bool(supports_thinking),
            "created_at": now,
            "updated_at": now,
        }

        rows = self._read_models(pid)
        rows.append(row)
        self._write_models(pid, rows)

        env_map = self._read_env(pid)
        env_map[env_key] = (api_key or "").strip()
        self._write_env(pid, env_map)
        return self._public_row(row, has_api_key=bool(env_map[env_key]))

    def delete_model(self, profile_id: str | None, model_id: str) -> bool:
        target = (model_id or "").strip()
        if not target:
            return False
        rows = self._read_models(profile_id)
        keep: list[dict[str, Any]] = []
        removed: dict[str, Any] | None = None
        for row in rows:
            if row["id"] == target:
                removed = row
                continue
            keep.append(row)
        if removed is None:
            return False
        self._write_models(profile_id, keep)

        env_map = self._read_env(profile_id)
        env_map.pop(removed["api_key_env"], None)
        self._write_env(profile_id, env_map)
        return True

    def get_model_secret(self, profile_id: str | None, model_id: str) -> str | None:
        target = (model_id or "").strip()
        if not target:
            return None
        rows = self._read_models(profile_id)
        row = next((r for r in rows if r["id"] == target), None)
        if row is None:
            return None
        env_map = self._read_env(profile_id)
        secret = env_map.get(row["api_key_env"])
        if secret is None:
            return None
        return secret

    def update_selection_related_fields(
        self,
        profile_id: str | None,
        model_id: str,
        *,
        last_selected_at: str | None = None,
    ) -> dict[str, Any] | None:
        target = (model_id or "").strip()
        if not target:
            return None
        rows = self._read_models(profile_id)
        env_map = self._read_env(profile_id)
        updated: dict[str, Any] | None = None
        now = _utc_now_iso()
        selection_time = (last_selected_at or "").strip() or now
        for row in rows:
            if row["id"] != target:
                continue
            row["last_selected_at"] = selection_time
            row["updated_at"] = now
            updated = row
            break
        if updated is None:
            return None
        self._write_models(profile_id, rows)
        has_api_key = bool(env_map.get(updated["api_key_env"], "").strip())
        return self._public_row(updated, has_api_key=has_api_key)


def resolve_global_base_model() -> dict[str, Any]:
    """Resolve the global built-in base model descriptor.

    The model is sourced from global config/env and injected into each user's
    model list as read-only.
    """
    cfg = resolve_config_env_vars(load_config())
    model_name = (cfg.agents.defaults.model or "").strip() or GLOBAL_BASE_MODEL_NAME
    api_base = cfg.get_api_base(model_name)
    api_key = cfg.get_api_key(model_name)
    provider_name = cfg.get_provider_name(model_name)
    # Preserve current UX: DeepSeek base model should expose thinking toggle.
    supports_thinking = bool(provider_name == "deepseek")
    return {
        "id": GLOBAL_BASE_MODEL_ID,
        "name": GLOBAL_BASE_MODEL_NAME,
        "model_name": model_name,
        "base_url": api_base or "",
        "supports_thinking": supports_thinking,
        "readonly": True,
        "source": "global_base",
        "has_api_key": bool((api_key or "").strip()),
    }
