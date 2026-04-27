"""Tests for profile-scoped model storage."""

from __future__ import annotations

import json
from pathlib import Path

from nanobot.profile import model_store as ms


def _use_temp_workspace(monkeypatch, tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ms, "_workspace_root", lambda: workspace)
    return workspace


def _create_store(monkeypatch, tmp_path: Path) -> ms.ModelStore:
    _use_temp_workspace(monkeypatch, tmp_path)
    return ms.ModelStore()


def test_add_model_writes_metadata_and_secret_separately(monkeypatch, tmp_path: Path) -> None:
    workspace = _use_temp_workspace(monkeypatch, tmp_path)
    store = ms.ModelStore()

    row = store.add_model(
        "alice",
        name="DeepSeek-V4-Flash",
        model_name="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        api_key="sk-abc-123",
        supports_thinking=True,
    )

    assert row["name"] == "DeepSeek-V4-Flash"
    assert row["supports_thinking"] is True
    assert row["has_api_key"] is True
    assert "api_key" not in row

    models_path = workspace / "users" / "alice" / "models.json"
    payload = json.loads(models_path.read_text(encoding="utf-8"))
    assert len(payload["models"]) == 1
    saved = payload["models"][0]
    assert saved["api_key_env"].startswith("NANOBOT_PROFILE_ALICE_MODEL_")
    assert "api_key" not in saved
    assert "sk-abc-123" not in models_path.read_text(encoding="utf-8")

    env_path = workspace / "users" / "alice" / ".env.models"
    env_text = env_path.read_text(encoding="utf-8")
    assert "sk-abc-123" in env_text
    assert saved["api_key_env"] in env_text


def test_list_models_never_returns_plain_api_key(monkeypatch, tmp_path: Path) -> None:
    store = _create_store(monkeypatch, tmp_path)
    store.add_model(
        "alice",
        name="No Leak",
        model_name="gpt-4o",
        base_url="https://api.openai.com/v1",
        api_key="sk-very-secret",
        supports_thinking=False,
    )

    rows = store.list_models("alice")

    assert len(rows) == 1
    assert rows[0]["has_api_key"] is True
    assert "api_key" not in rows[0]
    assert "sk-very-secret" not in json.dumps(rows, ensure_ascii=False)


def test_get_model_secret_reads_from_env_file(monkeypatch, tmp_path: Path) -> None:
    store = _create_store(monkeypatch, tmp_path)
    row = store.add_model(
        "alice",
        name="Secret Read",
        model_name="qwen-plus",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="sk-secret-value",
        supports_thinking=False,
    )

    secret = store.get_model_secret("alice", row["id"])
    missing = store.get_model_secret("alice", "missing-id")

    assert secret == "sk-secret-value"
    assert missing is None


def test_profile_isolation_between_model_lists(monkeypatch, tmp_path: Path) -> None:
    store = _create_store(monkeypatch, tmp_path)
    a = store.add_model(
        "alice",
        name="Alice Model",
        model_name="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        api_key="alice-key",
        supports_thinking=False,
    )
    b = store.add_model(
        "bob",
        name="Bob Model",
        model_name="gpt-4.1",
        base_url="https://api.openai.com/v1",
        api_key="bob-key",
        supports_thinking=False,
    )

    alice_rows = store.list_models("alice")
    bob_rows = store.list_models("bob")

    assert [r["id"] for r in alice_rows] == [a["id"]]
    assert [r["id"] for r in bob_rows] == [b["id"]]
    assert store.get_model_secret("alice", b["id"]) is None
    assert store.get_model_secret("bob", a["id"]) is None


def test_delete_model_removes_metadata_and_secret(monkeypatch, tmp_path: Path) -> None:
    workspace = _use_temp_workspace(monkeypatch, tmp_path)
    store = ms.ModelStore()
    row = store.add_model(
        "alice",
        name="Delete Me",
        model_name="claude-3-7-sonnet",
        base_url="https://api.anthropic.com/v1",
        api_key="anthropic-secret",
        supports_thinking=True,
    )

    deleted = store.delete_model("alice", row["id"])
    deleted_again = store.delete_model("alice", row["id"])

    assert deleted is True
    assert deleted_again is False
    assert store.list_models("alice") == []
    assert store.get_model_secret("alice", row["id"]) is None

    env_text = (workspace / "users" / "alice" / ".env.models").read_text(encoding="utf-8")
    assert "anthropic-secret" not in env_text
    assert row["api_key_env"] not in env_text


def test_update_selection_related_fields_sets_last_selected_at(monkeypatch, tmp_path: Path) -> None:
    store = _create_store(monkeypatch, tmp_path)
    row = store.add_model(
        "alice",
        name="Selectable",
        model_name="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        api_key="sk-key",
        supports_thinking=False,
    )

    updated = store.update_selection_related_fields(
        "alice",
        row["id"],
        last_selected_at="2026-04-26T00:00:00+00:00",
    )

    assert updated is not None
    assert updated["last_selected_at"] == "2026-04-26T00:00:00+00:00"
    all_rows = store.list_models("alice")
    assert all_rows[0]["last_selected_at"] == "2026-04-26T00:00:00+00:00"


def test_profile_id_is_required(monkeypatch, tmp_path: Path) -> None:
    store = _create_store(monkeypatch, tmp_path)

    try:
        store.list_models("")
    except ValueError as exc:
        assert str(exc) == "profile_id is required"
    else:
        raise AssertionError("expected ValueError for empty profile_id")


def test_resolve_global_base_model_uses_global_config(monkeypatch, tmp_path: Path) -> None:
    _use_temp_workspace(monkeypatch, tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "global-deepseek-key")

    model = ms.resolve_global_base_model()

    assert model["id"] == ms.GLOBAL_BASE_MODEL_ID
    assert model["name"] == ms.GLOBAL_BASE_MODEL_NAME
    assert model["model_name"] == "deepseek-v4-flash"
    assert model["source"] == "global_base"
    assert model["readonly"] is True
    assert model["has_api_key"] is True


def test_model_store_recovers_from_invalid_models_json(monkeypatch, tmp_path: Path) -> None:
    workspace = _use_temp_workspace(monkeypatch, tmp_path)
    store = ms.ModelStore()
    models_path = workspace / "users" / "alice" / "models.json"
    models_path.parent.mkdir(parents=True, exist_ok=True)
    models_path.write_text("{ broken", encoding="utf-8")

    rows = store.list_models("alice")

    assert rows == []
    payload = json.loads(models_path.read_text(encoding="utf-8"))
    assert payload == {"models": []}


def test_model_store_deduplicates_and_sanitizes_rows(monkeypatch, tmp_path: Path) -> None:
    workspace = _use_temp_workspace(monkeypatch, tmp_path)
    store = ms.ModelStore()
    models_path = workspace / "users" / "alice" / "models.json"
    models_path.parent.mkdir(parents=True, exist_ok=True)
    models_path.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "id": "same-id",
                        "name": " A ",
                        "model_name": "m1",
                        "base_url": "https://example.com",
                        "api_key_env": "K1",
                        "supports_thinking": "yes",
                    },
                    {
                        "id": "same-id",
                        "name": "duplicate",
                        "model_name": "m2",
                        "base_url": "https://example.org",
                        "api_key_env": "K2",
                    },
                    {"invalid": True},
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    rows = store.list_models("alice")

    assert len(rows) == 1
    assert rows[0]["id"] == "same-id"
    assert rows[0]["name"] == "A"
    assert rows[0]["supports_thinking"] is True


def test_read_models_skips_write_when_data_unchanged(monkeypatch, tmp_path: Path) -> None:
    workspace = _use_temp_workspace(monkeypatch, tmp_path)
    store = ms.ModelStore()
    row = store.add_model(
        "alice",
        name="M",
        model_name="m",
        base_url="https://x.com",
        api_key="k",
        supports_thinking=False,
    )
    models_path = workspace / "users" / "alice" / "models.json"
    payload_before = models_path.read_text(encoding="utf-8")

    writes = {"count": 0}
    orig_write = store._write_models

    def _spy_write(profile_id, rows):
        writes["count"] += 1
        return orig_write(profile_id, rows)

    monkeypatch.setattr(store, "_write_models", _spy_write)
    rows = store.list_models("alice")

    assert len(rows) == 1
    assert rows[0]["id"] == row["id"]
    assert writes["count"] == 0
    assert models_path.read_text(encoding="utf-8") == payload_before
