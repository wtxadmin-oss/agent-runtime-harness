"""Tests for persistent profile store behavior."""

from __future__ import annotations

import json
from pathlib import Path

from nanobot.profile import store


def _use_temp_workspace(monkeypatch, tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "_workspace_root", lambda: workspace)
    return workspace


def test_list_profiles_initializes_default_file(monkeypatch, tmp_path: Path) -> None:
    workspace = _use_temp_workspace(monkeypatch, tmp_path)

    rows = store.list_profiles()

    assert rows == []
    path = workspace / "users" / "profiles.json"
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["default_profile"] == ""


def test_create_profile_persists_and_deduplicates_slug(monkeypatch, tmp_path: Path) -> None:
    _use_temp_workspace(monkeypatch, tmp_path)

    p1 = store.create_profile("My User")
    p2 = store.create_profile("My User")

    assert p1.id == "my-user"
    assert p2.id == "my-user-2"
    rows = store.list_profiles()
    ids = {r["id"] for r in rows}
    assert "my-user" in ids
    assert "my-user-2" in ids


def test_normalize_profile_id_accepts_created_profile(monkeypatch, tmp_path: Path) -> None:
    _use_temp_workspace(monkeypatch, tmp_path)
    created = store.create_profile("Charlie")

    assert store.normalize_profile_id(created.id) == created.id
    assert store.normalize_profile_id("missing") == created.id
    assert store.normalize_profile_id(None) == created.id


def test_create_profile_bootstraps_isolated_memory_files(monkeypatch, tmp_path: Path) -> None:
    workspace = _use_temp_workspace(monkeypatch, tmp_path)
    created = store.create_profile("Isolated")

    profile_root = workspace / "users" / created.id
    assert (profile_root / "SOUL.md").exists()
    assert (profile_root / "USER.md").exists()
    assert (profile_root / "memory" / "MEMORY.md").exists()
    assert (profile_root / "memory" / "history.jsonl").exists()


def test_delete_profile_purges_user_dir_and_scoped_sessions(monkeypatch, tmp_path: Path) -> None:
    workspace = _use_temp_workspace(monkeypatch, tmp_path)
    created = store.create_profile("Neo")

    user_dir = workspace / "users" / created.id
    (user_dir / "memory").mkdir(parents=True, exist_ok=True)
    (user_dir / "memory" / "MEMORY.md").write_text("neo-memory", encoding="utf-8")
    sessions = workspace / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    scoped = sessions / f"websocket_{created.id}_chat-1.jsonl"
    scoped.write_text('{"messages":[]}\n', encoding="utf-8")
    other = sessions / "websocket_demo_alice_chat-1.jsonl"
    other.write_text('{"messages":[]}\n', encoding="utf-8")

    assert store.delete_profile(created.id) is True
    assert created.id not in {r["id"] for r in store.list_profiles()}
    assert not user_dir.exists()
    assert not scoped.exists()
    assert other.exists()


def test_delete_profile_returns_false_when_missing(monkeypatch, tmp_path: Path) -> None:
    _use_temp_workspace(monkeypatch, tmp_path)
    assert store.delete_profile("missing-profile") is False


def test_recreate_same_profile_id_starts_with_fresh_memory(monkeypatch, tmp_path: Path) -> None:
    workspace = _use_temp_workspace(monkeypatch, tmp_path)
    created = store.create_profile("Zhangsan")
    memory_file = workspace / "users" / created.id / "memory" / "MEMORY.md"
    memory_file.write_text("old-memory", encoding="utf-8")

    assert store.delete_profile(created.id) is True

    recreated = store.create_profile("Zhangsan")
    recreated_memory = workspace / "users" / recreated.id / "memory" / "MEMORY.md"
    assert recreated.id == created.id
    assert recreated_memory.exists()
    assert recreated_memory.read_text(encoding="utf-8") != "old-memory"
