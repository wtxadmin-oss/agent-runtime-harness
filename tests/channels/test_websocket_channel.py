"""Unit and lightweight integration tests for the WebSocket channel."""

import asyncio
import functools
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import websockets
from websockets.exceptions import ConnectionClosed
from websockets.frames import Close

from nanobot.bus.events import OutboundMessage
from nanobot.channels.websocket import (
    WebSocketChannel,
    WebSocketConfig,
    _is_valid_chat_id,
    _issue_route_secret_matches,
    _normalize_config_path,
    _normalize_http_path,
    _parse_envelope,
    _parse_inbound_payload,
    _parse_query,
    _parse_request_path,
)
from nanobot.profile import resolve_global_base_model
from nanobot.session.manager import SessionManager

# -- Shared helpers (aligned with test_websocket_integration.py) ---------------

_PORT = 29876


def _ch(bus: Any, **kw: Any) -> WebSocketChannel:
    cfg: dict[str, Any] = {
        "enabled": True,
        "allowFrom": ["*"],
        "host": "127.0.0.1",
        "port": _PORT,
        "path": "/ws",
        "websocketRequiresToken": False,
    }
    cfg.update(kw)
    return WebSocketChannel(cfg, bus)


@pytest.fixture()
def bus() -> MagicMock:
    b = MagicMock()
    b.publish_inbound = AsyncMock()
    return b


async def _http_get(url: str, headers: dict[str, str] | None = None) -> httpx.Response:
    """Run GET in a thread to avoid blocking the asyncio loop shared with websockets."""
    return await asyncio.to_thread(
        functools.partial(httpx.get, url, headers=headers or {}, timeout=5.0)
    )


def test_normalize_http_path_strips_trailing_slash_except_root() -> None:
    assert _normalize_http_path("/chat/") == "/chat"
    assert _normalize_http_path("/chat?x=1") == "/chat"
    assert _normalize_http_path("/") == "/"


def test_parse_request_path_matches_normalize_and_query() -> None:
    path, query = _parse_request_path("/ws/?token=secret&client_id=u1")
    assert path == _normalize_http_path("/ws/?token=secret&client_id=u1")
    assert query == _parse_query("/ws/?token=secret&client_id=u1")


def test_normalize_config_path_matches_request() -> None:
    assert _normalize_config_path("/ws/") == "/ws"
    assert _normalize_config_path("/") == "/"


def test_parse_query_extracts_token_and_client_id() -> None:
    query = _parse_query("/?token=secret&client_id=u1")
    assert query.get("token") == ["secret"]
    assert query.get("client_id") == ["u1"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("plain", "plain"),
        ('{"content": "hi"}', "hi"),
        ('{"text": "there"}', "there"),
        ('{"message": "x"}', "x"),
        ("  ", None),
        ("{}", None),
    ],
)
def test_parse_inbound_payload(raw: str, expected: str | None) -> None:
    assert _parse_inbound_payload(raw) == expected


def test_parse_inbound_invalid_json_falls_back_to_raw_string() -> None:
    assert _parse_inbound_payload("{not json") == "{not json"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"content": ""}', None),           # empty string content
        ('{"content": 123}', None),          # non-string content
        ('{"content": "  "}', None),         # whitespace-only content
        ('["hello"]', '["hello"]'),           # JSON array: not a dict, treated as plain text
        ('{"unknown_key": "val"}', None),    # unrecognized key
        ('{"content": null}', None),         # null content
    ],
)
def test_parse_inbound_payload_edge_cases(raw: str, expected: str | None) -> None:
    assert _parse_inbound_payload(raw) == expected


def test_web_socket_config_path_must_start_with_slash() -> None:
    with pytest.raises(ValueError, match='path must start with "/"'):
        WebSocketConfig(path="bad")


def test_ssl_context_requires_both_cert_and_key_files() -> None:
    bus = MagicMock()
    channel = WebSocketChannel(
        {"enabled": True, "allowFrom": ["*"], "sslCertfile": "/tmp/c.pem", "sslKeyfile": ""},
        bus,
    )
    with pytest.raises(ValueError, match="ssl_certfile and ssl_keyfile"):
        channel._build_ssl_context()


def test_default_config_includes_safe_bind_and_streaming() -> None:
    defaults = WebSocketChannel.default_config()
    assert defaults["enabled"] is False
    assert defaults["host"] == "127.0.0.1"
    assert defaults["streaming"] is True
    assert defaults["allowFrom"] == ["*"]
    assert defaults.get("tokenIssuePath", "") == ""


def test_token_issue_path_must_differ_from_websocket_path() -> None:
    with pytest.raises(ValueError, match="token_issue_path must differ"):
        WebSocketConfig(path="/ws", token_issue_path="/ws")


def test_issue_route_secret_matches_bearer_and_header() -> None:
    from websockets.datastructures import Headers

    secret = "my-secret"
    bearer_headers = Headers([("Authorization", "Bearer my-secret")])
    assert _issue_route_secret_matches(bearer_headers, secret) is True
    x_headers = Headers([("X-Nanobot-Auth", "my-secret")])
    assert _issue_route_secret_matches(x_headers, secret) is True
    wrong = Headers([("Authorization", "Bearer other")])
    assert _issue_route_secret_matches(wrong, secret) is False


def test_issue_route_secret_matches_empty_secret() -> None:
    from websockets.datastructures import Headers

    # Empty secret always returns True regardless of headers
    assert _issue_route_secret_matches(Headers([]), "") is True
    assert _issue_route_secret_matches(Headers([("Authorization", "Bearer anything")]), "") is True


@pytest.mark.asyncio
async def test_send_delivers_json_message_with_media_and_reply() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus)
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "chat-1")

    msg = OutboundMessage(
        channel="websocket",
        chat_id="chat-1",
        content="hello",
        reply_to="m1",
        media=["/tmp/a.png"],
    )
    await channel.send(msg)

    mock_ws.send.assert_awaited_once()
    payload = json.loads(mock_ws.send.call_args[0][0])
    assert payload["event"] == "message"
    assert payload["chat_id"] == "chat-1"
    assert payload["text"] == "hello"
    assert payload["reply_to"] == "m1"
    assert payload["media"] == ["/tmp/a.png"]


@pytest.mark.asyncio
async def test_send_missing_connection_is_noop_without_error() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus)
    msg = OutboundMessage(channel="websocket", chat_id="missing", content="x")
    await channel.send(msg)


@pytest.mark.asyncio
async def test_send_removes_connection_on_connection_closed() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus)
    mock_ws = AsyncMock()
    mock_ws.send.side_effect = ConnectionClosed(Close(1006, ""), Close(1006, ""), True)
    channel._attach(mock_ws, "chat-1")

    msg = OutboundMessage(channel="websocket", chat_id="chat-1", content="hello")
    await channel.send(msg)

    assert "chat-1" not in channel._subs
    assert mock_ws not in channel._conn_chats


@pytest.mark.asyncio
async def test_send_delta_removes_connection_on_connection_closed() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"], "streaming": True}, bus)
    mock_ws = AsyncMock()
    mock_ws.send.side_effect = ConnectionClosed(Close(1006, ""), Close(1006, ""), True)
    channel._attach(mock_ws, "chat-1")

    await channel.send_delta("chat-1", "chunk", {"_stream_delta": True, "_stream_id": "s1"})

    assert "chat-1" not in channel._subs
    assert mock_ws not in channel._conn_chats


@pytest.mark.asyncio
async def test_send_delta_emits_delta_and_stream_end() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"], "streaming": True}, bus)
    mock_ws = AsyncMock()
    channel._attach(mock_ws, "chat-1")

    await channel.send_delta("chat-1", "part", {"_stream_delta": True, "_stream_id": "sid"})
    await channel.send_delta("chat-1", "", {"_stream_end": True, "_stream_id": "sid"})

    assert mock_ws.send.await_count == 2
    first = json.loads(mock_ws.send.call_args_list[0][0][0])
    second = json.loads(mock_ws.send.call_args_list[1][0][0])
    assert first["event"] == "delta"
    assert first["chat_id"] == "chat-1"
    assert first["text"] == "part"
    assert first["stream_id"] == "sid"
    assert second["event"] == "stream_end"
    assert second["chat_id"] == "chat-1"
    assert second["stream_id"] == "sid"


@pytest.mark.asyncio
async def test_send_non_connection_closed_exception_is_raised() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus)
    mock_ws = AsyncMock()
    mock_ws.send.side_effect = RuntimeError("unexpected")
    channel._attach(mock_ws, "chat-1")

    msg = OutboundMessage(channel="websocket", chat_id="chat-1", content="hello")
    with pytest.raises(RuntimeError, match="unexpected"):
        await channel.send(msg)


@pytest.mark.asyncio
async def test_send_delta_missing_connection_is_noop() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"], "streaming": True}, bus)
    # No exception, no error — just a no-op
    await channel.send_delta("nonexistent", "chunk", {"_stream_delta": True, "_stream_id": "s1"})


@pytest.mark.asyncio
async def test_stop_is_idempotent() -> None:
    bus = MagicMock()
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus)
    # stop() before start() should not raise
    await channel.stop()
    await channel.stop()


@pytest.mark.asyncio
async def test_end_to_end_client_receives_ready_and_agent_sees_inbound(bus: MagicMock) -> None:
    port = 29876
    channel = _ch(bus, port=port)

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=tester") as client:
            ready_raw = await client.recv()
            ready = json.loads(ready_raw)
            assert ready["event"] == "ready"
            assert ready["client_id"] == "tester"
            chat_id = ready["chat_id"]

            await client.send(json.dumps({"content": "ping from client"}))
            await asyncio.sleep(0.08)

            bus.publish_inbound.assert_awaited()
            inbound = bus.publish_inbound.call_args[0][0]
            assert inbound.channel == "websocket"
            assert inbound.sender_id == "tester"
            assert inbound.chat_id == chat_id
            assert inbound.content == "ping from client"
            assert inbound.session_key_override == f"websocket:demo_alice:{chat_id}"

            await client.send("plain text frame")
            await asyncio.sleep(0.08)
            assert bus.publish_inbound.await_count >= 2
            second = [c[0][0] for c in bus.publish_inbound.call_args_list][-1]
            assert second.content == "plain text frame"
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_token_rejects_handshake_when_mismatch(bus: MagicMock) -> None:
    port = 29877
    channel = _ch(bus, port=port, path="/", token="secret")

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        with pytest.raises(websockets.exceptions.InvalidStatus) as excinfo:
            async with websockets.connect(f"ws://127.0.0.1:{port}/?token=wrong"):
                pass
        assert excinfo.value.response.status_code == 401
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_wrong_path_returns_404(bus: MagicMock) -> None:
    port = 29878
    channel = _ch(bus, port=port)

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        with pytest.raises(websockets.exceptions.InvalidStatus) as excinfo:
            async with websockets.connect(f"ws://127.0.0.1:{port}/other"):
                pass
        assert excinfo.value.response.status_code == 404
    finally:
        await channel.stop()
        await server_task


def test_registry_discovers_websocket_channel() -> None:
    from nanobot.channels.registry import load_channel_class

    cls = load_channel_class("websocket")
    assert cls.name == "websocket"


@pytest.mark.asyncio
async def test_http_route_issues_token_then_websocket_requires_it(bus: MagicMock) -> None:
    port = 29879
    channel = _ch(
        bus, port=port,
        tokenIssuePath="/auth/token",
        tokenIssueSecret="route-secret",
        websocketRequiresToken=True,
    )

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        deny = await _http_get(f"http://127.0.0.1:{port}/auth/token")
        assert deny.status_code == 401

        issue = await _http_get(
            f"http://127.0.0.1:{port}/auth/token",
            headers={"Authorization": "Bearer route-secret"},
        )
        assert issue.status_code == 200
        token = issue.json()["token"]
        assert token.startswith("nbwt_")

        with pytest.raises(websockets.exceptions.InvalidStatus) as missing_token:
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=x"):
                pass
        assert missing_token.value.response.status_code == 401

        uri = f"ws://127.0.0.1:{port}/ws?token={token}&client_id=caller"
        async with websockets.connect(uri) as client:
            ready = json.loads(await client.recv())
            assert ready["event"] == "ready"
            assert ready["client_id"] == "caller"

        with pytest.raises(websockets.exceptions.InvalidStatus) as reuse:
            async with websockets.connect(uri):
                pass
        assert reuse.value.response.status_code == 401
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_bootstrap_profile_token_binds_websocket_session_scope(
    bus: MagicMock,
) -> None:
    port = 29895
    channel = _ch(bus, port=port, websocketRequiresToken=True, path="/")

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        boot = await _http_get(
            f"http://127.0.0.1:{port}/webui/bootstrap?profile_id=demo_bob"
        )
        assert boot.status_code == 200
        token = boot.json()["token"]

        async with websockets.connect(
            f"ws://127.0.0.1:{port}/?token={token}&client_id=scope-check"
        ) as client:
            ready = json.loads(await client.recv())
            chat_id = ready["chat_id"]
            await client.send("hello")
            await asyncio.sleep(0.1)
            inbound = bus.publish_inbound.call_args[0][0]
            assert inbound.chat_id == chat_id
            assert inbound.session_key_override == f"websocket:demo_bob:{chat_id}"
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_end_to_end_server_pushes_streaming_deltas_to_client(bus: MagicMock) -> None:
    port = 29880
    channel = _ch(bus, port=port, streaming=True)

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=stream-tester") as client:
            ready_raw = await client.recv()
            ready = json.loads(ready_raw)
            chat_id = ready["chat_id"]

            # Server pushes deltas directly
            await channel.send_delta(
                chat_id, "Hello ", {"_stream_delta": True, "_stream_id": "s1"}
            )
            await channel.send_delta(
                chat_id, "world", {"_stream_delta": True, "_stream_id": "s1"}
            )
            await channel.send_delta(
                chat_id, "", {"_stream_end": True, "_stream_id": "s1"}
            )

            delta1 = json.loads(await client.recv())
            assert delta1["event"] == "delta"
            assert delta1["text"] == "Hello "
            assert delta1["stream_id"] == "s1"

            delta2 = json.loads(await client.recv())
            assert delta2["event"] == "delta"
            assert delta2["text"] == "world"
            assert delta2["stream_id"] == "s1"

            end = json.loads(await client.recv())
            assert end["event"] == "stream_end"
            assert end["stream_id"] == "s1"
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_token_issue_rejects_when_at_capacity(bus: MagicMock) -> None:
    port = 29881
    channel = _ch(bus, port=port, tokenIssuePath="/auth/token", tokenIssueSecret="s")

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        # Fill issued tokens to capacity
        channel._issued_tokens = {
            f"nbwt_fill_{i}": (time.monotonic() + 300, "demo_alice")
            for i in range(channel._MAX_ISSUED_TOKENS)
        }

        resp = await _http_get(
            f"http://127.0.0.1:{port}/auth/token",
            headers={"Authorization": "Bearer s"},
        )
        assert resp.status_code == 429
        data = resp.json()
        assert "error" in data
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_allow_from_rejects_unauthorized_client_id(bus: MagicMock) -> None:
    port = 29882
    channel = _ch(bus, port=port, allowFrom=["alice", "bob"])

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        with pytest.raises(websockets.exceptions.InvalidStatus) as exc_info:
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=eve"):
                pass
        assert exc_info.value.response.status_code == 403
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_client_id_truncation(bus: MagicMock) -> None:
    port = 29883
    channel = _ch(bus, port=port)

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        long_id = "x" * 200
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id={long_id}") as client:
            ready = json.loads(await client.recv())
            assert ready["client_id"] == "x" * 128
            assert len(ready["client_id"]) == 128
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_non_utf8_binary_frame_ignored(bus: MagicMock) -> None:
    port = 29884
    channel = _ch(bus, port=port)

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=bin-test") as client:
            await client.recv()  # consume ready
            # Send non-UTF-8 bytes
            await client.send(b"\xff\xfe\xfd")
            await asyncio.sleep(0.05)
            # publish_inbound should NOT have been called
            bus.publish_inbound.assert_not_awaited()
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_static_token_accepts_issued_token_as_fallback(bus: MagicMock) -> None:
    port = 29885
    channel = _ch(
        bus, port=port,
        token="static-secret",
        tokenIssuePath="/auth/token",
        tokenIssueSecret="route-secret",
    )

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        # Get an issued token
        resp = await _http_get(
            f"http://127.0.0.1:{port}/auth/token",
            headers={"Authorization": "Bearer route-secret"},
        )
        assert resp.status_code == 200
        issued_token = resp.json()["token"]

        # Connect using issued token (not the static one)
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token={issued_token}&client_id=caller") as client:
            ready = json.loads(await client.recv())
            assert ready["event"] == "ready"
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_allow_from_empty_list_denies_all(bus: MagicMock) -> None:
    port = 29886
    channel = _ch(bus, port=port, allowFrom=[])

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        with pytest.raises(websockets.exceptions.InvalidStatus) as exc_info:
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=anyone"):
                pass
        assert exc_info.value.response.status_code == 403
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_websocket_requires_token_without_issue_path(bus: MagicMock) -> None:
    """When websocket_requires_token is True but no token or issue path configured, all connections are rejected."""
    port = 29887
    channel = _ch(bus, port=port, websocketRequiresToken=True)

    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        # No token at all → 401
        with pytest.raises(websockets.exceptions.InvalidStatus) as exc_info:
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=u"):
                pass
        assert exc_info.value.response.status_code == 401

        # Wrong token → 401
        with pytest.raises(websockets.exceptions.InvalidStatus) as exc_info:
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=u&token=wrong"):
                pass
        assert exc_info.value.response.status_code == 401
    finally:
        await channel.stop()
        await server_task


# -- Multi-chat multiplexing -------------------------------------------------
#
# The multiplex protocol lets one WS connection route N logical chats over
# typed envelopes (`new_chat` / `attach` / `message`). Legacy frames must keep
# working on the connection's default chat_id.


@pytest.mark.asyncio
async def test_multiplex_legacy_still_works(bus: MagicMock) -> None:
    port = 29930
    channel = _ch(bus, port=port)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=legacy") as client:
            ready = json.loads(await client.recv())
            default_chat = ready["chat_id"]

            # Plain text frame routes to default chat_id
            await client.send("hello from legacy")
            await asyncio.sleep(0.1)
            inbound = bus.publish_inbound.call_args[0][0]
            assert inbound.chat_id == default_chat
            assert inbound.content == "hello from legacy"

            # {"content": ...} frame routes to default chat_id
            await client.send(json.dumps({"content": "structured legacy"}))
            await asyncio.sleep(0.1)
            assert bus.publish_inbound.call_args[0][0].chat_id == default_chat
            assert bus.publish_inbound.call_args[0][0].content == "structured legacy"

            # Outbound still reaches the legacy client, with chat_id annotated
            await channel.send(
                OutboundMessage(channel="websocket", chat_id=default_chat, content="reply")
            )
            reply = json.loads(await client.recv())
            assert reply["event"] == "message"
            assert reply["chat_id"] == default_chat
            assert reply["text"] == "reply"
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_multiplex_new_chat_roundtrip(bus: MagicMock) -> None:
    port = 29931
    channel = _ch(bus, port=port)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=mp") as client:
            ready = json.loads(await client.recv())
            default_chat = ready["chat_id"]

            await client.send(json.dumps({"type": "new_chat"}))
            attached = json.loads(await client.recv())
            assert attached["event"] == "attached"
            new_chat = attached["chat_id"]
            assert new_chat and new_chat != default_chat

            # Send on the new chat via typed envelope
            await client.send(
                json.dumps({"type": "message", "chat_id": new_chat, "content": "hi on new"})
            )
            await asyncio.sleep(0.1)
            inbound = bus.publish_inbound.call_args[0][0]
            assert inbound.chat_id == new_chat
            assert inbound.content == "hi on new"

            # Server pushes a message back; chat_id must match
            await channel.send(
                OutboundMessage(channel="websocket", chat_id=new_chat, content="ok")
            )
            reply = json.loads(await client.recv())
            assert reply["event"] == "message"
            assert reply["chat_id"] == new_chat
            assert reply["text"] == "ok"
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_multiplex_two_chats_isolated(bus: MagicMock) -> None:
    port = 29932
    channel = _ch(bus, port=port)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=two") as client:
            await client.recv()  # ready

            await client.send(json.dumps({"type": "new_chat"}))
            chat_a = json.loads(await client.recv())["chat_id"]
            await client.send(json.dumps({"type": "new_chat"}))
            chat_b = json.loads(await client.recv())["chat_id"]
            assert chat_a != chat_b

            # Push A → client sees A only (FIFO over the single WS).
            await channel.send(
                OutboundMessage(channel="websocket", chat_id=chat_a, content="for-A")
            )
            msg_a = json.loads(await client.recv())
            assert msg_a["chat_id"] == chat_a
            assert msg_a["text"] == "for-A"

            # Push B → client sees B only.
            await channel.send(
                OutboundMessage(channel="websocket", chat_id=chat_b, content="for-B")
            )
            msg_b = json.loads(await client.recv())
            assert msg_b["chat_id"] == chat_b
            assert msg_b["text"] == "for-B"
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_multiplex_invalid_frames_return_error(bus: MagicMock) -> None:
    port = 29933
    channel = _ch(bus, port=port)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=bad") as client:
            await client.recv()  # ready

            # attach with bad chat_id
            await client.send(json.dumps({"type": "attach", "chat_id": "has space"}))
            err1 = json.loads(await client.recv())
            assert err1["event"] == "error"

            # message with missing content
            await client.send(json.dumps({"type": "message", "chat_id": "abc", "content": ""}))
            err2 = json.loads(await client.recv())
            assert err2["event"] == "error"

            # unknown type
            await client.send(json.dumps({"type": "nope"}))
            err3 = json.loads(await client.recv())
            assert err3["event"] == "error"

            # Connection survives: legacy frame still works.
            await client.send("still-alive")
            await asyncio.sleep(0.1)
            bus.publish_inbound.assert_awaited()
            assert bus.publish_inbound.call_args[0][0].content == "still-alive"
    finally:
        await channel.stop()
        await server_task


@pytest.mark.asyncio
async def test_multiplex_cleanup_on_disconnect(bus: MagicMock) -> None:
    port = 29934
    channel = _ch(bus, port=port)
    server_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.3)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?client_id=dc") as client:
            ready = json.loads(await client.recv())
            default_chat = ready["chat_id"]
            await client.send(json.dumps({"type": "new_chat"}))
            extra_chat = json.loads(await client.recv())["chat_id"]
            assert default_chat in channel._subs
            assert extra_chat in channel._subs
        # Client gone. Server-side tracking must be empty.
        await asyncio.sleep(0.2)
        assert default_chat not in channel._subs
        assert extra_chat not in channel._subs
        assert not channel._conn_chats
        assert not channel._conn_default
    finally:
        await channel.stop()
        await server_task


def test_parse_envelope_detects_typed_frames() -> None:
    assert _parse_envelope('{"type":"new_chat"}') == {"type": "new_chat"}
    env = _parse_envelope('{"type":"message","chat_id":"abc","content":"hi"}')
    assert env == {"type": "message", "chat_id": "abc", "content": "hi"}


def test_parse_envelope_rejects_legacy_and_garbage() -> None:
    # No `type` field → legacy, caller falls back to _parse_inbound_payload.
    assert _parse_envelope('{"content":"hi"}') is None
    assert _parse_envelope("plain text") is None
    assert _parse_envelope("{broken") is None
    assert _parse_envelope("[1,2,3]") is None
    # Non-string `type` is not a valid envelope.
    assert _parse_envelope('{"type":123}') is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("abc", True),
        ("a1b2_c:d-e", True),
        ("x" * 64, True),
        ("unified:default", True),
        ("", False),
        ("x" * 65, False),
        ("has space", False),
        ("a/b", False),
        ("a.b", False),
        (None, False),
        (123, False),
    ],
)
def test_is_valid_chat_id(value: Any, expected: bool) -> None:
    assert _is_valid_chat_id(value) is expected


def test_model_list_includes_global_base_and_user_models(tmp_path) -> None:
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    sm = SessionManager(tmp_path)
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, session_manager=sm)
    conn = AsyncMock()
    channel._conn_profile[conn] = "alice"
    channel._model_store.add_model(
        "alice",
        name="User Model",
        model_name="gpt-4o",
        base_url="https://api.openai.com/v1",
        api_key="sk-user",
        supports_thinking=False,
    )

    asyncio.run(channel._dispatch_envelope(conn, "u1", {"type": "model_list"}))

    assert conn.send.await_count == 1
    payload = json.loads(conn.send.call_args[0][0])
    assert payload["event"] == "model_list"
    assert any(m.get("source") == "global_base" for m in payload["models"])
    assert any(m.get("source") == "user_custom" for m in payload["models"])


def test_model_select_persists_and_message_uses_selected_model(tmp_path) -> None:
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    sm = SessionManager(tmp_path)
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, session_manager=sm)
    conn = AsyncMock()
    channel._conn_profile[conn] = "alice"
    row = channel._model_store.add_model(
        "alice",
        name="User Model",
        model_name="gpt-4o",
        base_url="https://api.openai.com/v1",
        api_key="sk-user",
        supports_thinking=True,
    )

    asyncio.run(
        channel._dispatch_envelope(
            conn,
            "u1",
            {"type": "model_select", "chat_id": "chat1", "model_id": row["id"]},
        )
    )
    asyncio.run(
        channel._dispatch_envelope(
            conn,
            "u1",
            {"type": "message", "chat_id": "chat1", "content": "hello"},
        )
    )

    inbound = bus.publish_inbound.call_args[0][0]
    assert inbound.chat_id == "chat1"
    assert inbound.metadata.get("_model_id") == row["id"]

    asyncio.run(channel._dispatch_envelope(conn, "u1", {"type": "attach", "chat_id": "chat1"}))
    attached = json.loads(conn.send.call_args[0][0])
    assert attached["event"] == "attached"
    assert attached.get("selected_model_id") == row["id"]
    assert attached.get("thinking_supported") is True
    assert attached.get("thinking_recipe_ready") is False
    assert attached.get("thinking_unavailable_reason") == "missing_recipe"


def test_attach_clears_deleted_selected_model(tmp_path) -> None:
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    sm = SessionManager(tmp_path)
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, session_manager=sm)
    conn = AsyncMock()
    channel._conn_profile[conn] = "alice"
    row = channel._model_store.add_model(
        "alice",
        name="Transient Model",
        model_name="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
        api_key="sk-user",
        supports_thinking=False,
    )
    asyncio.run(
        channel._dispatch_envelope(
            conn,
            "u1",
            {"type": "model_select", "chat_id": "chat1", "model_id": row["id"]},
        )
    )
    assert channel._model_store.delete_model("alice", row["id"]) is True

    asyncio.run(channel._dispatch_envelope(conn, "u1", {"type": "attach", "chat_id": "chat1"}))

    attached = json.loads(conn.send.call_args[0][0])
    assert attached["event"] == "attached"
    assert attached.get("selected_model_id") is None
    session = sm.get_or_create("websocket:alice:chat1")
    assert "selected_model_id" not in session.metadata


def test_websocket_model_store_uses_session_manager_workspace(tmp_path) -> None:
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    sm = SessionManager(tmp_path)
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, session_manager=sm)
    row = channel._model_store.add_model(
        "alice",
        name="Workspace Scoped",
        model_name="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        api_key="sk-user",
        supports_thinking=True,
    )

    expected_path = tmp_path / "users" / "alice" / "models.json"
    assert expected_path.exists()
    payload = json.loads(expected_path.read_text(encoding="utf-8"))
    assert any(m.get("id") == row["id"] for m in payload.get("models", []))


def test_thinking_toggle_emits_recipe_required_event(tmp_path) -> None:
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    sm = SessionManager(tmp_path)
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, session_manager=sm)
    conn = AsyncMock()
    channel._conn_profile[conn] = "alice"
    row = channel._model_store.add_model(
        "alice",
        name="Thinkable",
        model_name="qwen3-plus",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="sk-user",
        supports_thinking=True,
    )
    asyncio.run(
        channel._dispatch_envelope(
            conn,
            "u1",
            {"type": "model_select", "chat_id": "chat1", "model_id": row["id"]},
        )
    )
    conn.send.reset_mock()

    asyncio.run(
        channel._dispatch_envelope(
            conn,
            "u1",
            {"type": "thinking_toggle", "chat_id": "chat1", "enabled": True},
        )
    )

    payload = json.loads(conn.send.call_args[0][0])
    assert payload["event"] == "thinking_recipe_required"
    assert payload["chat_id"] == "chat1"
    assert payload.get("reason") == "missing_recipe"


def test_thinking_toggle_for_global_base_does_not_require_recipe(tmp_path) -> None:
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    sm = SessionManager(tmp_path)
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, session_manager=sm)
    conn = AsyncMock()
    channel._conn_profile[conn] = "alice"

    asyncio.run(
        channel._dispatch_envelope(
            conn,
            "u1",
            {"type": "thinking_toggle", "chat_id": "chat1", "enabled": True},
        )
    )

    assert conn.send.await_count == 0


def test_thinking_recipe_submit_and_confirm_persists_global_recipe(tmp_path) -> None:
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    sm = SessionManager(tmp_path)
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, session_manager=sm)
    conn = AsyncMock()
    channel._conn_profile[conn] = "alice"

    asyncio.run(
        channel._dispatch_envelope(
            conn,
            "u1",
            {
                "type": "thinking_recipe_submit",
                "chat_id": "chat1",
                "doc_url": "https://api.deepseek.com/docs/reasoning",
                "snippet": "thinking: { enabled: true, effort: 'high' }",
            },
        )
    )
    submit_events = [json.loads(args[0][0]) for args in conn.send.await_args_list]
    progress_stages = [
        ev.get("stage")
        for ev in submit_events
        if ev.get("event") == "thinking_recipe_progress"
    ]
    assert progress_stages == ["submitted", "extracting", "compiling", "preview_ready"]
    preview = next(ev for ev in submit_events if ev.get("event") == "thinking_recipe_preview")
    assert preview["event"] == "thinking_recipe_preview"
    preview_id = preview["preview_id"]
    assert preview_id
    assert preview["recipe"]["validated"] is True

    conn.send.reset_mock()
    # Confirm should emit saved event once.
    asyncio.run(
        channel._dispatch_envelope(
            conn,
            "u1",
            {
                "type": "thinking_recipe_confirm",
                "chat_id": "chat1",
                "preview_id": preview_id,
            },
        )
    )
    saved = json.loads(conn.send.call_args[0][0])
    assert saved["event"] == "thinking_recipe_saved"
    assert isinstance(saved.get("model_signature"), str) and saved["model_signature"]

    recipe_path = tmp_path / "system" / "thinking_recipes.json"
    assert recipe_path.exists()
    evidence_dir = tmp_path / "system" / "thinking_evidence"
    assert evidence_dir.exists()
    assert list(evidence_dir.glob("*.json"))


def test_thinking_recipe_submit_emits_conflict_event_before_preview(tmp_path, monkeypatch) -> None:
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    sm = SessionManager(tmp_path)
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, session_manager=sm)
    conn = AsyncMock()
    channel._conn_profile[conn] = "alice"

    async def _fake_submit(**kwargs):
        on_progress = kwargs.get("on_progress")
        if callable(on_progress):
            await on_progress({"stage": "submitted", "message": "request accepted"})
            await on_progress({"stage": "extracting", "message": "extracting"})
            await on_progress({"stage": "compiling", "message": "compiling"})
            await on_progress({"stage": "preview_ready", "message": "ready"})
        return {
            "preview_id": "pv-conflict",
            "recipe": {"validated": True, "controls": {"enabled_param": "enable_thinking"}},
            "evidence": [{"kind": "url", "source": "https://example.com/docs"}],
            "orchestrator": {
                "conflict": True,
                "decision": "url",
                "conflict_detail": {
                    "decision": "url",
                    "scoring": {"url": {"credibility": 0.9}},
                    "evidence": [{"kind": "url", "source": "https://example.com/docs"}],
                },
            },
        }

    monkeypatch.setattr(channel._thinking_recipe_orchestrator, "submit", _fake_submit)

    asyncio.run(
        channel._dispatch_envelope(
            conn,
            "u1",
            {
                "type": "thinking_recipe_submit",
                "chat_id": "chat1",
                "doc_url": "https://example.com/docs",
                "snippet": "thinking.enabled=true",
            },
        )
    )

    events = [json.loads(args[0][0]) for args in conn.send.await_args_list]
    conflict_idx = next(i for i, ev in enumerate(events) if ev.get("event") == "thinking_recipe_conflict_detected")
    preview_idx = next(i for i, ev in enumerate(events) if ev.get("event") == "thinking_recipe_preview")
    assert conflict_idx < preview_idx
    conflict = events[conflict_idx]
    assert conflict.get("decision") == "url"
    assert isinstance(conflict.get("scoring"), dict)
    assert isinstance(conflict.get("evidence"), list)


def test_thinking_toggle_does_not_require_recipe_when_recipe_exists(tmp_path) -> None:
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    sm = SessionManager(tmp_path)
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, session_manager=sm)
    conn = AsyncMock()
    channel._conn_profile[conn] = "alice"

    base = resolve_global_base_model()
    compiled = channel._thinking_recipe_store.compile(
        model_name=base["model_name"],
        base_url=base["base_url"],
        doc_url="https://api.deepseek.com/docs/reasoning",
        snippet="thinking.enabled = true",
    )
    channel._thinking_recipe_store.save(
        recipe=compiled["recipe"],
        evidence=compiled["evidence"],
    )

    asyncio.run(
        channel._dispatch_envelope(
            conn,
            "u1",
            {"type": "thinking_toggle", "chat_id": "chat1", "enabled": True},
        )
    )

    assert conn.send.await_count == 0


def test_model_add_with_thinking_requires_doc_or_snippet(tmp_path) -> None:
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    sm = SessionManager(tmp_path)
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, session_manager=sm)
    conn = AsyncMock()
    channel._conn_profile[conn] = "alice"

    asyncio.run(
        channel._dispatch_envelope(
            conn,
            "u1",
            {
                "type": "model_add",
                "name": "Need evidence",
                "model_name": "qwen3-plus",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "api_key": "sk-user",
                "supports_thinking": True,
            },
        )
    )

    payload = json.loads(conn.send.call_args[0][0])
    assert payload["event"] == "error"
    assert payload["detail"] == "thinking_source_required"


def test_model_add_with_thinking_source_auto_persists_recipe(tmp_path) -> None:
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    sm = SessionManager(tmp_path)
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, session_manager=sm)
    conn = AsyncMock()
    channel._conn_profile[conn] = "alice"

    asyncio.run(
        channel._dispatch_envelope(
            conn,
            "u1",
            {
                "type": "model_add",
                "name": "Thinkable",
                "model_name": "qwen3-plus",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "api_key": "sk-user",
                "supports_thinking": True,
                "thinking_doc_url": "https://help.aliyun.com/zh/model-studio/qwen",
            },
        )
    )

    payload = json.loads(conn.send.call_args[0][0])
    assert payload["event"] == "model_saved"
    recipe = channel._thinking_recipe_store.lookup(
        model_name="qwen3-plus",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    assert recipe is not None
    assert recipe.get("validated") is True


def test_message_thinking_missing_recipe_emits_required_and_falls_back(tmp_path) -> None:
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    sm = SessionManager(tmp_path)
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, session_manager=sm)
    conn = AsyncMock()
    channel._conn_profile[conn] = "alice"
    row = channel._model_store.add_model(
        "alice",
        name="Thinkable",
        model_name="qwen3-plus",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="sk-user",
        supports_thinking=True,
    )

    asyncio.run(
        channel._dispatch_envelope(
            conn,
            "u1",
            {
                "type": "message",
                "chat_id": "chat1",
                "content": "hello",
                "model_id": row["id"],
                "thinking": {"enabled": True},
            },
        )
    )

    assert conn.send.await_count == 1
    required = json.loads(conn.send.call_args[0][0])
    assert required["event"] == "thinking_recipe_required"
    assert required["reason"] == "missing_recipe"

    inbound = bus.publish_inbound.call_args[0][0]
    assert inbound.metadata.get("_thinking_enabled") is None
    assert inbound.metadata.get("_reasoning_effort") is None


def test_message_thinking_unvalidated_recipe_emits_required_and_falls_back(tmp_path) -> None:
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    sm = SessionManager(tmp_path)
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, session_manager=sm)
    conn = AsyncMock()
    channel._conn_profile[conn] = "alice"

    base = resolve_global_base_model()
    compiled = channel._thinking_recipe_store.compile(
        model_name=base["model_name"],
        base_url=base["base_url"],
        doc_url=None,
        snippet="thinking.enabled=true",
    )
    compiled["recipe"]["validated"] = False
    channel._thinking_recipe_store.save(
        recipe=compiled["recipe"],
        evidence=compiled["evidence"],
    )

    asyncio.run(
        channel._dispatch_envelope(
            conn,
            "u1",
            {
                "type": "message",
                "chat_id": "chat1",
                "content": "hello",
                "thinking": {"enabled": True, "effort": "max"},
            },
        )
    )

    required = json.loads(conn.send.call_args[0][0])
    assert required["event"] == "thinking_recipe_required"
    assert required["reason"] == "unvalidated_recipe"

    inbound = bus.publish_inbound.call_args[0][0]
    assert inbound.metadata.get("_thinking_enabled") is None
    assert inbound.metadata.get("_reasoning_effort") is None


def test_message_thinking_with_valid_recipe_sets_metadata(tmp_path) -> None:
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    sm = SessionManager(tmp_path)
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, session_manager=sm)
    conn = AsyncMock()
    channel._conn_profile[conn] = "alice"

    base = resolve_global_base_model()
    compiled = channel._thinking_recipe_store.compile(
        model_name=base["model_name"],
        base_url=base["base_url"],
        doc_url=None,
        snippet="thinking.enabled=true",
    )
    channel._thinking_recipe_store.save(
        recipe=compiled["recipe"],
        evidence=compiled["evidence"],
    )

    asyncio.run(
        channel._dispatch_envelope(
            conn,
            "u1",
            {
                "type": "message",
                "chat_id": "chat1",
                "content": "hello",
                "thinking": {"enabled": True},
            },
        )
    )

    assert conn.send.await_count == 0
    inbound = bus.publish_inbound.call_args[0][0]
    assert inbound.metadata.get("_thinking_enabled") is True
    assert inbound.metadata.get("_reasoning_effort") == "high"


def test_message_thinking_model_not_supported_emits_error_and_falls_back(tmp_path) -> None:
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    sm = SessionManager(tmp_path)
    channel = WebSocketChannel({"enabled": True, "allowFrom": ["*"]}, bus, session_manager=sm)
    conn = AsyncMock()
    channel._conn_profile[conn] = "alice"
    row = channel._model_store.add_model(
        "alice",
        name="NoThink",
        model_name="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
        api_key="sk-user",
        supports_thinking=False,
    )

    asyncio.run(
        channel._dispatch_envelope(
            conn,
            "u1",
            {
                "type": "message",
                "chat_id": "chat1",
                "content": "hello",
                "model_id": row["id"],
                "thinking": {"enabled": True},
            },
        )
    )

    event = json.loads(conn.send.call_args[0][0])
    assert event["event"] == "thinking_recipe_error"
    assert event["detail"] == "model_does_not_support_thinking"
    inbound = bus.publish_inbound.call_args[0][0]
    assert inbound.metadata.get("_thinking_enabled") is None
    assert inbound.metadata.get("_reasoning_effort") is None
