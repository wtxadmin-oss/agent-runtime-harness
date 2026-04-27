from __future__ import annotations

from pathlib import Path

from nanobot.session.manager import Session, SessionManager


def test_purge_profile_sessions_removes_cache_and_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    sm = SessionManager(workspace)

    s1 = Session(key="websocket:alice:chat-1")
    s2 = Session(key="websocket:alice:chat-2")
    s3 = Session(key="websocket:bob:chat-3")
    sm.save(s1)
    sm.save(s2)
    sm.save(s3)

    assert "websocket:alice:chat-1" in sm._cache
    assert "websocket:alice:chat-2" in sm._cache
    assert "websocket:bob:chat-3" in sm._cache

    deleted = sm.purge_profile_sessions("alice")
    assert deleted >= 2

    assert "websocket:alice:chat-1" not in sm._cache
    assert "websocket:alice:chat-2" not in sm._cache
    assert "websocket:bob:chat-3" in sm._cache

    files = {p.name for p in (workspace / "sessions").glob("*.jsonl")}
    assert "websocket_bob_chat-3.jsonl" in files
    assert "websocket_alice_chat-1.jsonl" not in files
    assert "websocket_alice_chat-2.jsonl" not in files
