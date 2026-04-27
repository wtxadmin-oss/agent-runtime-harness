"""Configuration loading utilities."""

import json
import os
import re
from pathlib import Path
from typing import Any

import pydantic
from loguru import logger
from pydantic import BaseModel

from nanobot.config.schema import AgentDefaults, Config, ProviderConfig

# Global variable to store current config path (for multi-instance support)
_current_config_path: Path | None = None
_dotenv_loaded_files: set[Path] = set()
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def set_config_path(path: Path) -> None:
    """Set the current config path (used to derive data directory)."""
    global _current_config_path
    _current_config_path = path


def get_config_path() -> Path:
    """Get the configuration file path."""
    if _current_config_path:
        return _current_config_path
    return Path.home() / ".nanobot" / "config.json"


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file or create default.

    Args:
        config_path: Optional path to config file. Uses default if not provided.

    Returns:
        Loaded configuration object.
    """
    path = config_path or get_config_path()
    _load_dotenv_files(path)

    config = Config()
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data = _migrate_config(data)
            config = Config.model_validate(data)
        except (json.JSONDecodeError, ValueError, pydantic.ValidationError) as e:
            logger.warning(f"Failed to load config from {path}: {e}")
            logger.warning("Using default configuration.")

    _apply_deepseek_defaults(config)
    _apply_ssrf_whitelist(config)
    return config


def _iter_provider_configs(config: Config) -> list[ProviderConfig]:
    providers = config.providers
    rows: list[ProviderConfig] = []
    for field_name in type(providers).model_fields:
        value = getattr(providers, field_name, None)
        if isinstance(value, ProviderConfig):
            rows.append(value)
    return rows


def _has_any_provider_key(config: Config) -> bool:
    return any((p.api_key or "").strip() for p in _iter_provider_configs(config))


def _apply_deepseek_defaults(config: Config) -> None:
    """Apply env-driven DeepSeek defaults when user hasn't configured providers yet."""
    deepseek_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not deepseek_key:
        return

    had_provider_key = _has_any_provider_key(config)
    deepseek = config.providers.deepseek
    if not (deepseek.api_key or "").strip():
        deepseek.api_key = deepseek_key
    if not (deepseek.api_base or "").strip():
        deepseek.api_base = "https://api.deepseek.com"

    defaults = config.agents.defaults
    schema_defaults = AgentDefaults()
    model_untouched = defaults.model == schema_defaults.model
    provider_untouched = defaults.provider == schema_defaults.provider
    if deepseek.api_key and not had_provider_key and model_untouched and provider_untouched:
        defaults.provider = "deepseek"
        defaults.model = "deepseek-v4-flash"


def _apply_ssrf_whitelist(config: Config) -> None:
    """Apply SSRF whitelist from config to the network security module."""
    from nanobot.security.network import configure_ssrf_whitelist

    configure_ssrf_whitelist(config.tools.ssrf_whitelist)


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(mode="json", by_alias=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


_ENV_REF_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def resolve_config_env_vars(config: Config) -> Config:
    """Return *config* with ``${VAR}`` env-var references resolved.

    Walks in place so fields declared with ``exclude=True`` (e.g.
    ``DreamConfig.cron``) survive; returns the same instance when no
    references are present. Raises ``ValueError`` if a referenced
    variable is not set.
    """
    return _resolve_in_place(config)


def _resolve_in_place(obj: Any) -> Any:
    if isinstance(obj, str):
        new = _ENV_REF_PATTERN.sub(_env_replace, obj)
        return new if new != obj else obj
    if isinstance(obj, BaseModel):
        updates: dict[str, Any] = {}
        for name in type(obj).model_fields:
            old = getattr(obj, name)
            new = _resolve_in_place(old)
            if new is not old:
                updates[name] = new
        extras = obj.__pydantic_extra__
        new_extras: dict[str, Any] | None = None
        if extras:
            resolved = {k: _resolve_in_place(v) for k, v in extras.items()}
            if any(resolved[k] is not extras[k] for k in extras):
                new_extras = resolved
        if not updates and new_extras is None:
            return obj
        copy = obj.model_copy(update=updates) if updates else obj.model_copy()
        if new_extras is not None:
            copy.__pydantic_extra__ = new_extras
        return copy
    if isinstance(obj, dict):
        resolved = {k: _resolve_in_place(v) for k, v in obj.items()}
        return resolved if any(resolved[k] is not obj[k] for k in obj) else obj
    if isinstance(obj, list):
        resolved = [_resolve_in_place(v) for v in obj]
        return resolved if any(nv is not ov for nv, ov in zip(resolved, obj)) else obj
    return obj


def _resolve_env_vars(obj: object) -> object:
    """Recursively resolve ``${VAR}`` patterns in plain strings/dicts/lists."""
    if isinstance(obj, str):
        return _ENV_REF_PATTERN.sub(_env_replace, obj)
    if isinstance(obj, dict):
        return {k: _resolve_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_vars(v) for v in obj]
    return obj


def _env_replace(match: re.Match[str]) -> str:
    name = match.group(1)
    value = os.environ.get(name)
    if value is None:
        raise ValueError(
            f"Environment variable '{name}' referenced in config is not set"
        )
    return value


def _load_dotenv_files(config_path: Path | None) -> None:
    """Best-effort .env loading (non-overriding) from common locations."""
    candidates = []
    cfg_dir = (config_path or get_config_path()).parent
    candidates.append(cfg_dir / ".env")
    candidates.append(Path.cwd() / ".env")
    candidates.append(Path.home() / ".nanobot" / ".env")

    for path in candidates:
        resolved = path.expanduser().resolve()
        if resolved in _dotenv_loaded_files:
            continue
        if not resolved.is_file():
            continue
        _load_dotenv_file(resolved)
        _dotenv_loaded_files.add(resolved)


def _strip_wrapped_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        inner = value[1:-1]
        if value[0] == '"':
            return bytes(inner, "utf-8").decode("unicode_escape")
        return inner
    return value


def _load_dotenv_file(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

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
        value = _strip_wrapped_quotes(raw_val.strip())
        os.environ.setdefault(key, value)


def _migrate_config(data: dict) -> dict:
    """Migrate old config formats to current."""
    # Move tools.exec.restrictToWorkspace → tools.restrictToWorkspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")

    # Move tools.myEnabled / tools.mySet → tools.my.{enable, allowSet}.
    # The old flat keys shipped in the initial MyTool landing; wrapping them in a
    # sub-config keeps `web` / `exec` / `my` symmetric and gives room to grow.
    if "myEnabled" in tools or "mySet" in tools:
        my_cfg = tools.setdefault("my", {})
        if "myEnabled" in tools and "enable" not in my_cfg:
            my_cfg["enable"] = tools.pop("myEnabled")
        else:
            tools.pop("myEnabled", None)
        if "mySet" in tools and "allowSet" not in my_cfg:
            my_cfg["allowSet"] = tools.pop("mySet")
        else:
            tools.pop("mySet", None)

    return data
