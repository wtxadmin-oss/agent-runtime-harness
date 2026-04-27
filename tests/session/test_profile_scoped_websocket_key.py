"""Compatibility tests for profile-scoped websocket session keys."""

from __future__ import annotations

from pathlib import Path

from nanobot.session.manager import Session, SessionManager


def _seed_legacy_websocket_session(manager: SessionManager, key: str) -> Path:
    session = Session(key=key)
    session.add_message("user", "hello")
    manager.save(session)
    return manager._get_session_path(key)


def test_get_or_create_migrates_legacy_websocket_key(tmp_path: Path) -> None:
    manager = SessionManager(workspace=tmp_path)
    old_key = "websocket:chat-legacy"
    old_path = _seed_legacy_websocket_session(manager, old_key)
    manager.invalidate(old_key)

    new_key = "websocket:demo_alice:chat-legacy"
    migrated = manager.get_or_create(new_key)
    new_path = manager._get_session_path(new_key)

    assert migrated.key == new_key
    assert new_path.exists()
    assert not old_path.exists()


def test_read_session_file_migrates_legacy_websocket_key(tmp_path: Path) -> None:
    manager = SessionManager(workspace=tmp_path)
    old_key = "websocket:chat-read"
    old_path = _seed_legacy_websocket_session(manager, old_key)

    payload = manager.read_session_file("websocket:demo_alice:chat-read")

    assert payload is not None
    assert payload["key"] == "websocket:demo_alice:chat-read"
    assert not old_path.exists()


def test_delete_profile_scoped_key_removes_legacy_file(tmp_path: Path) -> None:
    manager = SessionManager(workspace=tmp_path)
    old_key = "websocket:chat-delete"
    old_path = _seed_legacy_websocket_session(manager, old_key)

    deleted = manager.delete_session("websocket:demo_alice:chat-delete")

    assert deleted is True
    assert not old_path.exists()
