"""End-to-end tests for the embedded webui's HTTP routes on the WebSocket channel."""

import asyncio
import functools
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from nanobot.channels.websocket import WebSocketChannel
from nanobot.profile import store as profile_store
from nanobot.session.manager import Session, SessionManager

_PORT = 29900


def _ch(
    bus: Any,
    *,
    session_manager: SessionManager | None = None,
    static_dist_path: Path | None = None,
    port: int = _PORT,
    **extra: Any,
) -> WebSocketChannel:
    cfg: dict[str, Any] = {
        "enabled": True,
        "allowFrom": ["*"],
        "host": "127.0.0.1",
        "port": port,
        "path": "/",
        "websocketRequiresToken": False,
    }
    cfg.update(extra)
    return WebSocketChannel(
        cfg,
        bus,
        session_manager=session_manager,
        static_dist_path=static_dist_path,
    )


@pytest.fixture()
def bus() -> MagicMock:
    b = MagicMock()
    b.publish_inbound = AsyncMock()
    return b


async def _http_get(
    url: str, headers: dict[str, str] | None = None
) -> httpx.Response:
    return await asyncio.to_thread(
        functools.partial(httpx.get, url, headers=headers or {}, timeout=5.0)
    )


def _seed_session(workspace: Path, key: str = "websocket:test") -> SessionManager:
    sm = SessionManager(workspace)
    s = Session(key=key)
    s.add_message("user", "hi")
    s.add_message("assistant", "hello back")
    sm.save(s)
    return sm


def _seed_many(workspace: Path, keys: list[str]) -> SessionManager:
    sm = SessionManager(workspace)
    for k in keys:
        s = Session(key=k)
        s.add_message("user", f"hi from {k}")
        sm.save(s)
    return sm


def _use_temp_profile_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Path:
    workspace = tmp_path / "profile-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(profile_store, "_workspace_root", lambda: workspace)
    return workspace


@pytest.mark.asyncio
async def test_bootstrap_returns_token_for_localhost(
    bus: MagicMock, tmp_path: Path
) -> None:
    sm = _seed_session(tmp_path)
    channel = _ch(bus, session_manager=sm, port=29901)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        resp = await _http_get("http://127.0.0.1:29901/webui/bootstrap")
        assert resp.status_code == 200
        body = resp.json()
        assert body["token"].startswith("nbwt_")
        assert body["ws_path"] == "/"
        assert body["expires_in"] > 0
        assert isinstance(body.get("model_name"), str)
        assert body["profile_id"] == "demo_alice"
        assert {p["id"] for p in body["profiles"]} == {"demo_alice", "demo_bob"}
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_bootstrap_accepts_profile_id_query(
    bus: MagicMock, tmp_path: Path
) -> None:
    sm = _seed_session(tmp_path)
    channel = _ch(bus, session_manager=sm, port=29911)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        resp = await _http_get(
            "http://127.0.0.1:29911/webui/bootstrap?profile_id=demo_bob"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["profile_id"] == "demo_bob"
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_profiles_routes_require_bearer_token(
    bus: MagicMock, tmp_path: Path
) -> None:
    sm = _seed_session(tmp_path)
    channel = _ch(bus, session_manager=sm, port=29914)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        deny_list = await _http_get("http://127.0.0.1:29914/api/profiles")
        assert deny_list.status_code == 401
        deny_create = await _http_get("http://127.0.0.1:29914/api/profiles/create?name=Neo")
        assert deny_create.status_code == 401
        deny_delete = await _http_get(
            "http://127.0.0.1:29914/api/profiles/delete?profile_id=demo_bob"
        )
        assert deny_delete.status_code == 401
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_profiles_list_and_create(
    bus: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_temp_profile_workspace(monkeypatch, tmp_path)
    sm = _seed_session(tmp_path)
    channel = _ch(bus, session_manager=sm, port=29915)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        boot = await _http_get(
            "http://127.0.0.1:29915/webui/bootstrap?profile_id=demo_bob"
        )
        token = boot.json()["token"]
        auth = {"Authorization": f"Bearer {token}"}

        listing = await _http_get("http://127.0.0.1:29915/api/profiles", headers=auth)
        assert listing.status_code == 200
        body = listing.json()
        assert body["current_profile_id"] == "demo_bob"
        assert {p["id"] for p in body["profiles"]} == {"demo_alice", "demo_bob"}

        created = await _http_get(
            "http://127.0.0.1:29915/api/profiles/create?name=Charlie%20User",
            headers=auth,
        )
        assert created.status_code == 200
        profile = created.json()["profile"]
        assert profile["id"] == "charlie-user"
        assert profile["name"] == "Charlie User"

        listing2 = await _http_get("http://127.0.0.1:29915/api/profiles", headers=auth)
        assert listing2.status_code == 200
        assert "charlie-user" in {p["id"] for p in listing2.json()["profiles"]}

        invalid = await _http_get(
            "http://127.0.0.1:29915/api/profiles/create?name=%20%20",
            headers=auth,
        )
        assert invalid.status_code == 400
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_profiles_delete_route(
    bus: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _use_temp_profile_workspace(monkeypatch, tmp_path)
    sm = _seed_session(tmp_path)
    channel = _ch(bus, session_manager=sm, port=29916)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        boot = await _http_get("http://127.0.0.1:29916/webui/bootstrap")
        token = boot.json()["token"]
        auth = {"Authorization": f"Bearer {token}"}

        created = await _http_get(
            "http://127.0.0.1:29916/api/profiles/create?name=Delete%20Me",
            headers=auth,
        )
        assert created.status_code == 200
        pid = created.json()["profile"]["id"]

        user_marker = workspace / "users" / pid / "memory" / "MEMORY.md"
        user_marker.parent.mkdir(parents=True, exist_ok=True)
        user_marker.write_text("delete-me", encoding="utf-8")
        session_marker = workspace / "sessions" / f"websocket_{pid}_abc.jsonl"
        session_marker.parent.mkdir(parents=True, exist_ok=True)
        session_marker.write_text('{"messages":[]}\n', encoding="utf-8")

        deleted = await _http_get(
            f"http://127.0.0.1:29916/api/profiles/delete?profile_id={pid}",
            headers=auth,
        )
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        assert deleted.json()["profile_id"] == pid
        assert not user_marker.exists()
        assert not session_marker.exists()

        listing = await _http_get("http://127.0.0.1:29916/api/profiles", headers=auth)
        assert listing.status_code == 200
        assert pid not in {p["id"] for p in listing.json()["profiles"]}

        deny_current = await _http_get(
            "http://127.0.0.1:29916/api/profiles/delete?profile_id=demo_alice",
            headers=auth,
        )
        assert deny_current.status_code == 400

        missing = await _http_get(
            "http://127.0.0.1:29916/api/profiles/delete?profile_id=missing",
            headers=auth,
        )
        assert missing.status_code == 404
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_sessions_routes_require_bearer_token(
    bus: MagicMock, tmp_path: Path
) -> None:
    sm = _seed_session(tmp_path, key="websocket:demo_alice:abc")
    channel = _ch(bus, session_manager=sm, port=29902)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        # Unauthenticated → 401.
        deny = await _http_get("http://127.0.0.1:29902/api/sessions")
        assert deny.status_code == 401

        # Mint a token via bootstrap, then call the API with it.
        boot = await _http_get("http://127.0.0.1:29902/webui/bootstrap")
        token = boot.json()["token"]
        auth = {"Authorization": f"Bearer {token}"}

        listing = await _http_get("http://127.0.0.1:29902/api/sessions", headers=auth)
        assert listing.status_code == 200
        keys = [s["key"] for s in listing.json()["sessions"]]
        assert "websocket:demo_alice:abc" in keys
        # Server stays an opaque source: filesystem paths must not leak to the wire.
        assert all("path" not in s for s in listing.json()["sessions"])

        msgs = await _http_get(
            "http://127.0.0.1:29902/api/sessions/websocket:demo_alice:abc/messages",
            headers=auth,
        )
        assert msgs.status_code == 200
        body = msgs.json()
        assert body["key"] == "websocket:demo_alice:abc"
        assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_sessions_list_only_returns_websocket_sessions_by_default(
    bus: MagicMock, tmp_path: Path
) -> None:
    # Seed a realistic multi-channel disk state: CLI, Slack, Lark and
    # websocket sessions all live in the same ``sessions/`` directory.
    sm = _seed_many(
        tmp_path,
        [
            "cli:direct",
            "slack:C123",
            "lark:oc_abc",
            "websocket:demo_alice:alpha",
            "websocket:demo_bob:beta",
        ],
    )
    channel = _ch(bus, session_manager=sm, port=29906)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        boot = await _http_get("http://127.0.0.1:29906/webui/bootstrap")
        token = boot.json()["token"]
        auth = {"Authorization": f"Bearer {token}"}

        listing = await _http_get(
            "http://127.0.0.1:29906/api/sessions", headers=auth
        )
        assert listing.status_code == 200
        keys = {s["key"] for s in listing.json()["sessions"]}
        # Only websocket-channel sessions are part of the webui surface; CLI /
        # Slack / Lark rows would be non-resumable from the browser.
        assert keys == {"websocket:demo_alice:alpha"}
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_sessions_are_scoped_by_profile_token(
    bus: MagicMock, tmp_path: Path
) -> None:
    sm = _seed_many(
        tmp_path,
        [
            "websocket:demo_alice:only-a",
            "websocket:demo_bob:only-b",
        ],
    )
    channel = _ch(bus, session_manager=sm, port=29912)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        boot_a = await _http_get(
            "http://127.0.0.1:29912/webui/bootstrap?profile_id=demo_alice"
        )
        auth_a = {"Authorization": f"Bearer {boot_a.json()['token']}"}
        list_a = await _http_get("http://127.0.0.1:29912/api/sessions", headers=auth_a)
        assert list_a.status_code == 200
        assert {s["key"] for s in list_a.json()["sessions"]} == {"websocket:demo_alice:only-a"}

        boot_b = await _http_get(
            "http://127.0.0.1:29912/webui/bootstrap?profile_id=demo_bob"
        )
        auth_b = {"Authorization": f"Bearer {boot_b.json()['token']}"}
        list_b = await _http_get("http://127.0.0.1:29912/api/sessions", headers=auth_b)
        assert list_b.status_code == 200
        assert {s["key"] for s in list_b.json()["sessions"]} == {"websocket:demo_bob:only-b"}
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_session_delete_removes_file(bus: MagicMock, tmp_path: Path) -> None:
    sm = _seed_session(tmp_path, key="websocket:demo_alice:doomed")
    channel = _ch(bus, session_manager=sm, port=29903)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        boot = await _http_get("http://127.0.0.1:29903/webui/bootstrap")
        token = boot.json()["token"]
        auth = {"Authorization": f"Bearer {token}"}

        path = sm._get_session_path("websocket:demo_alice:doomed")
        assert path.exists()
        resp = await _http_get(
            "http://127.0.0.1:29903/api/sessions/websocket:demo_alice:doomed/delete",
            headers=auth,
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        assert not path.exists()
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_session_routes_accept_percent_encoded_websocket_keys(
    bus: MagicMock, tmp_path: Path
) -> None:
    sm = _seed_session(tmp_path, key="websocket:demo_alice:encoded-key")
    channel = _ch(bus, session_manager=sm, port=29910)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        boot = await _http_get("http://127.0.0.1:29910/webui/bootstrap")
        token = boot.json()["token"]
        auth = {"Authorization": f"Bearer {token}"}

        msgs = await _http_get(
            "http://127.0.0.1:29910/api/sessions/websocket%3Ademo_alice%3Aencoded-key/messages",
            headers=auth,
        )
        assert msgs.status_code == 200
        assert msgs.json()["key"] == "websocket:demo_alice:encoded-key"

        path = sm._get_session_path("websocket:demo_alice:encoded-key")
        assert path.exists()
        deleted = await _http_get(
            "http://127.0.0.1:29910/api/sessions/websocket%3Ademo_alice%3Aencoded-key/delete",
            headers=auth,
        )
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        assert not path.exists()
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_session_routes_reject_non_websocket_keys(
    bus: MagicMock, tmp_path: Path
) -> None:
    sm = _seed_many(
        tmp_path,
        [
            "websocket:demo_alice:kept",
            "cli:direct",
            "slack:C123",
        ],
    )
    channel = _ch(bus, session_manager=sm, port=29909)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        boot = await _http_get("http://127.0.0.1:29909/webui/bootstrap")
        token = boot.json()["token"]
        auth = {"Authorization": f"Bearer {token}"}

        # The webui list already hides non-websocket sessions; handcrafted URLs
        # should hit the same boundary rather than exposing or deleting them.
        msgs = await _http_get(
            "http://127.0.0.1:29909/api/sessions/cli:direct/messages",
            headers=auth,
        )
        assert msgs.status_code == 404

        doomed = sm._get_session_path("slack:C123")
        assert doomed.exists()
        deny_delete = await _http_get(
            "http://127.0.0.1:29909/api/sessions/slack:C123/delete",
            headers=auth,
        )
        assert deny_delete.status_code == 404
        assert doomed.exists()
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_session_routes_reject_cross_profile_key(
    bus: MagicMock, tmp_path: Path
) -> None:
    sm = _seed_many(
        tmp_path,
        [
            "websocket:demo_alice:kept",
            "websocket:demo_bob:secret",
        ],
    )
    channel = _ch(bus, session_manager=sm, port=29913)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        boot = await _http_get(
            "http://127.0.0.1:29913/webui/bootstrap?profile_id=demo_alice"
        )
        token = boot.json()["token"]
        auth = {"Authorization": f"Bearer {token}"}

        msgs = await _http_get(
            "http://127.0.0.1:29913/api/sessions/websocket:demo_bob:secret/messages",
            headers=auth,
        )
        assert msgs.status_code == 404

        doomed = sm._get_session_path("websocket:demo_bob:secret")
        assert doomed.exists()
        deny_delete = await _http_get(
            "http://127.0.0.1:29913/api/sessions/websocket:demo_bob:secret/delete",
            headers=auth,
        )
        assert deny_delete.status_code == 404
        assert doomed.exists()
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_session_routes_reject_invalid_key(
    bus: MagicMock, tmp_path: Path
) -> None:
    sm = _seed_session(tmp_path)
    channel = _ch(bus, session_manager=sm, port=29904)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        boot = await _http_get("http://127.0.0.1:29904/webui/bootstrap")
        token = boot.json()["token"]
        auth = {"Authorization": f"Bearer {token}"}

        # Invalid characters in the key -> regex match fails -> 404
        # (route doesn't match, falls through to channel 404).
        resp = await _http_get(
            "http://127.0.0.1:29904/api/sessions/bad%20key/messages",
            headers=auth,
        )
        assert resp.status_code in {400, 404}
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_static_serves_index_when_dist_present(
    bus: MagicMock, tmp_path: Path
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>nbweb</title>")
    (dist / "favicon.svg").write_text("<svg/>")
    sm = _seed_session(tmp_path / "ws_state")
    channel = _ch(bus, session_manager=sm, static_dist_path=dist, port=29905)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        # Bare ``GET /`` is a browser opening the app: it must return the SPA
        # index.html, not the WS-upgrade handler's 401/426.
        root = await _http_get("http://127.0.0.1:29905/")
        assert root.status_code == 200
        assert "nbweb" in root.text
        asset = await _http_get("http://127.0.0.1:29905/favicon.svg")
        assert asset.status_code == 200
        assert "<svg" in asset.text
        # Unknown SPA route falls back to index.html.
        spa = await _http_get("http://127.0.0.1:29905/sessions/abc")
        assert spa.status_code == 200
        assert "nbweb" in spa.text
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_static_rejects_path_traversal(
    bus: MagicMock, tmp_path: Path
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("ok")
    secret = tmp_path / "secret.txt"
    secret.write_text("classified")
    channel = _ch(bus, static_dist_path=dist, port=29906)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        resp = await _http_get("http://127.0.0.1:29906/../secret.txt")
        # Normalized by httpx into /secret.txt → falls back to index.html, not 'classified'.
        assert "classified" not in resp.text
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_unknown_route_returns_404(bus: MagicMock) -> None:
    channel = _ch(bus, port=29907)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)
    try:
        resp = await _http_get("http://127.0.0.1:29907/api/unknown")
        assert resp.status_code == 404
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_api_token_pool_purges_expired(bus: MagicMock, tmp_path: Path) -> None:
    sm = _seed_session(tmp_path)
    channel = _ch(bus, session_manager=sm, port=29908)
    # Don't start a server — directly inject and validate.
    import time as _time
    channel._api_tokens["expired"] = (_time.monotonic() - 1, "demo_alice")
    channel._api_tokens["live"] = (_time.monotonic() + 60, "demo_bob")

    class _FakeReq:
        path = "/api/sessions"
        headers = {"Authorization": "Bearer expired"}

    assert channel._check_api_token(_FakeReq()) is None

    class _LiveReq:
        path = "/api/sessions"
        headers = {"Authorization": "Bearer live"}

    assert channel._check_api_token(_LiveReq()) == "demo_bob"
