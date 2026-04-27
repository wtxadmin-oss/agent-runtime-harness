"""Global thinking recipe and evidence storage.

Phase 4B keeps recipe/evidence global (shared by all profiles) so common
thinking parameter mappings can be reused across users.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.config.loader import load_config
from nanobot.utils.helpers import ensure_dir

_URL_TRAILING_SLASH_RE = re.compile(r"/+$")


def _workspace_root() -> Path:
    """Resolve current workspace path from active config."""
    try:
        return ensure_dir(load_config().workspace_path)
    except Exception as e:
        logger.warning("thinking recipe store fallback workspace path: {}", e)
        return ensure_dir(Path.home() / ".nanobot" / "workspace")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_base_url(base_url: str) -> str:
    return _URL_TRAILING_SLASH_RE.sub("", (base_url or "").strip())


def _model_signature(model_name: str, base_url: str) -> str:
    return f"{_normalize_base_url(base_url).lower()}::{(model_name or '').strip().lower()}"


def _snippet_excerpt(text: str, max_len: int = 1200) -> str:
    clean = (text or "").strip()
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 3] + "..."


class ThinkingRecipeStore:
    """Store global thinking recipes and their evidence records."""

    def __init__(self, workspace_root: Path | None = None) -> None:
        self._workspace_root = ensure_dir(workspace_root or _workspace_root())
        self._system_root = ensure_dir(self._workspace_root / "system")
        self._evidence_root = ensure_dir(self._system_root / "thinking_evidence")
        self._recipes_path = self._system_root / "thinking_recipes.json"

    @staticmethod
    def _default_payload() -> dict[str, Any]:
        return {"recipes": []}

    def _read_payload(self) -> dict[str, Any]:
        if not self._recipes_path.exists():
            payload = self._default_payload()
            self._write_payload(payload)
            return payload
        try:
            raw = json.loads(self._recipes_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                rows = raw.get("recipes")
                if isinstance(rows, list):
                    return {"recipes": rows}
        except Exception:
            logger.warning("thinking recipe store: invalid thinking_recipes.json, resetting")
        payload = self._default_payload()
        self._write_payload(payload)
        return payload

    def _write_payload(self, payload: dict[str, Any]) -> None:
        self._recipes_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def list_recipes(self) -> list[dict[str, Any]]:
        payload = self._read_payload()
        rows = payload.get("recipes", [])
        return rows if isinstance(rows, list) else []

    def lookup(self, *, model_name: str, base_url: str) -> dict[str, Any] | None:
        sig = _model_signature(model_name, base_url)
        for row in self.list_recipes():
            if isinstance(row, dict) and row.get("model_signature") == sig:
                return row
        return None

    @staticmethod
    def _infer_enabled_param(text: str) -> str | None:
        hay = text.lower()
        if "thinking_enabled" in hay:
            return "thinking_enabled"
        if "enable_thinking" in hay:
            return "enable_thinking"
        if "thinking.enabled" in hay or "thinking" in hay:
            return "thinking.enabled"
        return None

    @staticmethod
    def _infer_effort_param(text: str) -> str | None:
        hay = text.lower()
        if "reasoning_effort" in hay:
            return "reasoning_effort"
        if "thinking.effort" in hay or "effort" in hay:
            return "thinking.effort"
        return None

    def compile(
        self,
        *,
        model_name: str,
        base_url: str,
        doc_url: str | None,
        snippet: str | None,
    ) -> dict[str, Any]:
        clean_model = (model_name or "").strip()
        clean_base = _normalize_base_url(base_url)
        clean_url = (doc_url or "").strip()
        clean_snippet = (snippet or "").strip()
        if not clean_model:
            raise ValueError("model_name is required")
        if not clean_base:
            raise ValueError("base_url is required")
        if not clean_url and not clean_snippet:
            raise ValueError("doc_url_or_snippet_required")

        evidence: list[dict[str, Any]] = []
        if clean_url:
            evidence.append({
                "id": str(uuid.uuid4()),
                "type": "url",
                "source": clean_url,
                "captured_at": _utc_now_iso(),
            })
        if clean_snippet:
            evidence.append({
                "id": str(uuid.uuid4()),
                "type": "snippet",
                "source": "inline",
                "excerpt": _snippet_excerpt(clean_snippet),
                "captured_at": _utc_now_iso(),
            })

        combined_text = "\n".join(
            part for part in (clean_url, clean_snippet) if part
        )
        enabled_param = self._infer_enabled_param(combined_text)
        effort_param = self._infer_effort_param(combined_text)

        recipe = {
            "schema_version": 1,
            "model_signature": _model_signature(clean_model, clean_base),
            "model_name": clean_model,
            "base_url": clean_base,
            "validated": enabled_param is not None,
            "controls": {
                "enabled_param": enabled_param,
                "effort_param": effort_param,
                "allowed_efforts": ["high", "max"],
                "default_effort": "high",
            },
            "evidence_ids": [e["id"] for e in evidence],
            "updated_at": _utc_now_iso(),
        }
        return {
            "preview_id": str(uuid.uuid4()),
            "recipe": recipe,
            "evidence": evidence,
        }

    def save(self, *, recipe: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
        sig = recipe.get("model_signature")
        if not isinstance(sig, str) or not sig:
            raise ValueError("invalid_model_signature")
        now = _utc_now_iso()

        payload = self._read_payload()
        rows = payload.get("recipes")
        if not isinstance(rows, list):
            rows = []
            payload["recipes"] = rows
        kept: list[dict[str, Any]] = []
        replaced = False
        created_at: str | None = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("model_signature") == sig:
                replaced = True
                old_created = row.get("created_at")
                if isinstance(old_created, str) and old_created:
                    created_at = old_created
                continue
            kept.append(row)
        final = dict(recipe)
        final["created_at"] = created_at or now
        final["updated_at"] = now
        kept.append(final)
        payload["recipes"] = kept
        self._write_payload(payload)

        for item in evidence:
            if not isinstance(item, dict):
                continue
            ev_id = item.get("id")
            if not isinstance(ev_id, str) or not ev_id.strip():
                continue
            path = self._evidence_root / f"{ev_id}.json"
            path.write_text(
                json.dumps(item, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return {
            "model_signature": sig,
            "model_name": final.get("model_name"),
            "base_url": final.get("base_url"),
            "updated_at": final.get("updated_at"),
            "created": not replaced,
        }
