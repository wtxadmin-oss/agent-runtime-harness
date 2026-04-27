"""WebSocket server channel: nanobot acts as a WebSocket server and serves connected clients."""

from __future__ import annotations

import asyncio
import base64
import binascii
import email.utils
import hashlib
import hmac
import http
import json
import mimetypes
import re
import secrets
import ssl
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self
from urllib.parse import parse_qs, unquote, urlparse

from loguru import logger
from pydantic import Field, field_validator, model_validator
from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.paths import get_media_dir
from nanobot.config.schema import Base
from nanobot.profile.store import (
    create_profile,
    delete_profile,
    list_profiles,
    normalize_profile_id,
)
from nanobot.profile import (
    GLOBAL_BASE_MODEL_ID,
    ModelStore,
    ThinkingRecipeOrchestrator,
    ThinkingRecipeStore,
    resolve_global_base_model,
)
from nanobot.utils.media_decode import (
    FileSizeExceeded,
    save_base64_data_url,
)

if TYPE_CHECKING:
    from nanobot.session.manager import SessionManager


def _strip_trailing_slash(path: str) -> str:
    if len(path) > 1 and path.endswith("/"):
        return path.rstrip("/")
    return path or "/"


def _normalize_config_path(path: str) -> str:
    return _strip_trailing_slash(path)


class WebSocketConfig(Base):
    """WebSocket server channel configuration.

    Clients connect with URLs like ``ws://{host}:{port}{path}?client_id=...&token=...``.
    - ``client_id``: Used for ``allow_from`` authorization; if omitted, a value is generated and logged.
    - ``token``: If non-empty, the ``token`` query param may match this static secret; short-lived tokens
      from ``token_issue_path`` are also accepted.
    - ``token_issue_path``: If non-empty, **GET** (HTTP/1.1) to this path returns JSON
      ``{"token": "...", "expires_in": <seconds>}``; use ``?token=...`` when opening the WebSocket.
      Must differ from ``path`` (the WS upgrade path). If the client runs in the **same process** as
      nanobot and shares the asyncio loop, use a thread or async HTTP client for GET—do not call
      blocking ``urllib`` or synchronous ``httpx`` from inside a coroutine.
    - ``token_issue_secret``: If non-empty, token requests must send ``Authorization: Bearer <secret>`` or
      ``X-Nanobot-Auth: <secret>``.
    - ``websocket_requires_token``: If True, the handshake must include a valid token (static or issued and not expired).
    - Each connection has its own session: a unique ``chat_id`` maps to the agent session internally.
    - ``media`` field in outbound messages contains local filesystem paths; remote clients need a
      shared filesystem or an HTTP file server to access these files.
    """

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8765
    path: str = "/"
    token: str = ""
    token_issue_path: str = ""
    token_issue_secret: str = ""
    token_ttl_s: int = Field(default=300, ge=30, le=86_400)
    websocket_requires_token: bool = True
    allow_from: list[str] = Field(default_factory=lambda: ["*"])
    streaming: bool = True
    # Default 36 MB, upper 40 MB: supports up to 4 images at ~6 MB each after
    # client-side Worker normalization (see webui Composer). 4 × 6 MB × 1.37
    # (base64 overhead) + envelope framing stays under 36 MB; the 40 MB ceiling
    # leaves a small margin for sender slop without opening a DoS avenue.
    max_message_bytes: int = Field(default=37_748_736, ge=1024, le=41_943_040)
    ping_interval_s: float = Field(default=20.0, ge=5.0, le=300.0)
    ping_timeout_s: float = Field(default=20.0, ge=5.0, le=300.0)
    ssl_certfile: str = ""
    ssl_keyfile: str = ""

    @field_validator("path")
    @classmethod
    def path_must_start_with_slash(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError('path must start with "/"')
        return _normalize_config_path(value)

    @field_validator("token_issue_path")
    @classmethod
    def token_issue_path_format(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        if not value.startswith("/"):
            raise ValueError('token_issue_path must start with "/"')
        return _normalize_config_path(value)

    @model_validator(mode="after")
    def token_issue_path_differs_from_ws_path(self) -> Self:
        if not self.token_issue_path:
            return self
        if _normalize_config_path(self.token_issue_path) == _normalize_config_path(self.path):
            raise ValueError("token_issue_path must differ from path (the WebSocket upgrade path)")
        return self


def _http_json_response(data: dict[str, Any], *, status: int = 200) -> Response:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    headers = Headers(
        [
            ("Date", email.utils.formatdate(usegmt=True)),
            ("Connection", "close"),
            ("Content-Length", str(len(body))),
            ("Content-Type", "application/json; charset=utf-8"),
        ]
    )
    reason = http.HTTPStatus(status).phrase
    return Response(status, reason, headers, body)


def _read_webui_model_name() -> str | None:
    """Return the configured default model for readonly webui display."""
    try:
        from nanobot.config.loader import load_config

        model = load_config().agents.defaults.model.strip()
        return model or None
    except Exception as e:
        logger.debug("webui bootstrap could not load model name: {}", e)
        return None


def _parse_request_path(path_with_query: str) -> tuple[str, dict[str, list[str]]]:
    """Parse normalized path and query parameters in one pass."""
    parsed = urlparse("ws://x" + path_with_query)
    path = _strip_trailing_slash(parsed.path or "/")
    return path, parse_qs(parsed.query)


def _normalize_http_path(path_with_query: str) -> str:
    """Return the path component (no query string), with trailing slash normalized (root stays ``/``)."""
    return _parse_request_path(path_with_query)[0]


def _parse_query(path_with_query: str) -> dict[str, list[str]]:
    return _parse_request_path(path_with_query)[1]


def _query_first(query: dict[str, list[str]], key: str) -> str | None:
    """Return the first value for *key*, or None."""
    values = query.get(key)
    return values[0] if values else None


def _parse_inbound_payload(raw: str) -> str | None:
    """Parse a client frame into text; return None for empty or unrecognized content."""
    text = raw.strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(data, dict):
            for key in ("content", "text", "message"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            return None
        return None
    return text


# Accept UUIDs and short scoped keys like "unified:default". Keeps the capability
# namespace small enough to rule out path traversal / quote injection tricks.
_CHAT_ID_RE = re.compile(r"^[A-Za-z0-9_:-]{1,64}$")


def _is_valid_chat_id(value: Any) -> bool:
    return isinstance(value, str) and _CHAT_ID_RE.match(value) is not None


def _parse_envelope(raw: str) -> dict[str, Any] | None:
    """Return a typed envelope dict if the frame is a new-style JSON envelope, else None.

    A frame qualifies when it parses as a JSON object with a string ``type`` field.
    Legacy frames (plain text, or ``{"content": ...}`` without ``type``) return None;
    callers should fall back to :func:`_parse_inbound_payload` for those.
    """
    text = raw.strip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    t = data.get("type")
    if not isinstance(t, str):
        return None
    return data


# Per-message image limits. The server-side guard is a touch looser than the
# client's ``Worker`` normalization target (6 MB) — tolerate client slop, but
# still cap total ingress at ``_MAX_IMAGES_PER_MESSAGE * _MAX_IMAGE_BYTES``
# which fits comfortably inside ``max_message_bytes``.
_MAX_IMAGES_PER_MESSAGE = 4
_MAX_IMAGE_BYTES = 8 * 1024 * 1024

# Image MIME whitelist — matches the Composer's ``accept`` list. SVG is
# explicitly excluded to avoid the XSS surface inside embedded scripts.
_IMAGE_MIME_ALLOWED: frozenset[str] = frozenset({
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
})

_DATA_URL_MIME_RE = re.compile(r"^data:([^;]+);base64,", re.DOTALL)


def _extract_data_url_mime(url: str) -> str | None:
    """Return the MIME type of a ``data:<mime>;base64,...`` URL, else ``None``."""
    if not isinstance(url, str):
        return None
    m = _DATA_URL_MIME_RE.match(url)
    if not m:
        return None
    return m.group(1).strip().lower() or None


_LOCALHOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# Matches the legacy chat-id pattern but allows file-system-safe stems too,
# so the API can address sessions whose keys came from non-WebSocket channels.
_API_KEY_RE = re.compile(r"^[A-Za-z0-9_:.-]{1,128}$")


def _decode_api_key(raw_key: str) -> str | None:
    """Decode a percent-encoded API path segment, then validate the result."""
    key = unquote(raw_key)
    if _API_KEY_RE.match(key) is None:
        return None
    return key


def _is_localhost(connection: Any) -> bool:
    """Return True if *connection* originated from the loopback interface."""
    addr = getattr(connection, "remote_address", None)
    if not addr:
        return False
    host = addr[0] if isinstance(addr, tuple) else addr
    if not isinstance(host, str):
        return False
    # ``::ffff:127.0.0.1`` is loopback in IPv6-mapped form.
    if host.startswith("::ffff:"):
        host = host[7:]
    return host in _LOCALHOSTS


def _http_response(
    body: bytes,
    *,
    status: int = 200,
    content_type: str = "text/plain; charset=utf-8",
    extra_headers: list[tuple[str, str]] | None = None,
) -> Response:
    headers = [
        ("Date", email.utils.formatdate(usegmt=True)),
        ("Connection", "close"),
        ("Content-Length", str(len(body))),
        ("Content-Type", content_type),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    reason = http.HTTPStatus(status).phrase
    return Response(status, reason, Headers(headers), body)


def _http_error(status: int, message: str | None = None) -> Response:
    body = (message or http.HTTPStatus(status).phrase).encode("utf-8")
    return _http_response(body, status=status)


def _bearer_token(headers: Any) -> str | None:
    """Pull a Bearer token out of standard or query-style headers."""
    auth = headers.get("Authorization") or headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


def _is_websocket_upgrade(request: WsRequest) -> bool:
    """Detect an actual WS upgrade; plain HTTP GETs to the same path should fall through."""
    upgrade = request.headers.get("Upgrade") or request.headers.get("upgrade")
    connection = request.headers.get("Connection") or request.headers.get("connection")
    if not upgrade or "websocket" not in upgrade.lower():
        return False
    if not connection or "upgrade" not in connection.lower():
        return False
    return True


def _b64url_encode(data: bytes) -> str:
    """URL-safe base64 without padding — compact + friendly in URL paths."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    """Reverse of :func:`_b64url_encode`; caller handles ``ValueError``."""
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


# Allowed MIME types we actually serve from the media endpoint. Anything
# outside this set is degraded to ``application/octet-stream`` so an
# attacker who somehow gets a signed URL for an unexpected file type can't
# trick the browser into sniffing executable content.
_MEDIA_ALLOWED_MIMES: frozenset[str] = frozenset({
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
})


def _issue_route_secret_matches(headers: Any, configured_secret: str) -> bool:
    """Return True if the token-issue HTTP request carries credentials matching ``token_issue_secret``."""
    if not configured_secret:
        return True
    authorization = headers.get("Authorization") or headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
        return hmac.compare_digest(supplied, configured_secret)
    header_token = headers.get("X-Nanobot-Auth") or headers.get("x-nanobot-auth")
    if not header_token:
        return False
    return hmac.compare_digest(header_token.strip(), configured_secret)


class WebSocketChannel(BaseChannel):
    """Run a local WebSocket server; forward text/JSON messages to the message bus."""

    name = "websocket"
    display_name = "WebSocket"

    def __init__(
        self,
        config: Any,
        bus: MessageBus,
        *,
        session_manager: "SessionManager | None" = None,
        static_dist_path: Path | None = None,
    ):
        if isinstance(config, dict):
            config = WebSocketConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: WebSocketConfig = config
        # chat_id -> connections subscribed to it (fan-out target).
        self._subs: dict[str, set[Any]] = {}
        # connection -> chat_ids it is subscribed to (O(1) cleanup on disconnect).
        self._conn_chats: dict[Any, set[str]] = {}
        # connection -> default chat_id for legacy frames that omit routing.
        self._conn_default: dict[Any, str] = {}
        # connection -> profile id (Phase 1 default; Phase 2 binds token -> profile)
        self._conn_profile: dict[Any, str] = {}
        # Single-use tokens consumed at WebSocket handshake.
        # value: (expiry_monotonic_s, profile_id)
        self._issued_tokens: dict[str, tuple[float, str]] = {}
        # Multi-use tokens for the embedded webui's REST surface; checked but not consumed.
        # value: (expiry_monotonic_s, profile_id)
        self._api_tokens: dict[str, tuple[float, str]] = {}
        self._stop_event: asyncio.Event | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._session_manager = session_manager
        self._model_store = ModelStore(session_manager.workspace if session_manager else None)
        self._thinking_recipe_store = ThinkingRecipeStore(
            session_manager.workspace if session_manager else None
        )
        self._thinking_recipe_orchestrator = ThinkingRecipeOrchestrator(
            session_manager.workspace if session_manager else None
        )
        # connection -> preview_id -> {"recipe": {...}, "evidence": [...]}
        self._pending_recipe_previews: dict[Any, dict[str, dict[str, Any]]] = {}
        self._static_dist_path: Path | None = (
            static_dist_path.resolve() if static_dist_path is not None else None
        )
        # Process-local secret used to HMAC-sign media URLs. The signed URL is
        # the capability — anyone who holds a valid URL can fetch that one
        # file, nothing else. The secret regenerates on restart so links
        # become self-expiring (callers just refresh the session list).
        self._media_secret: bytes = secrets.token_bytes(32)

    # -- Subscription bookkeeping -------------------------------------------

    def _attach(self, connection: Any, chat_id: str) -> None:
        """Idempotently subscribe *connection* to *chat_id*."""
        self._subs.setdefault(chat_id, set()).add(connection)
        self._conn_chats.setdefault(connection, set()).add(chat_id)

    def _cleanup_connection(self, connection: Any) -> None:
        """Remove *connection* from every subscription set; safe to call multiple times."""
        chat_ids = self._conn_chats.pop(connection, set())
        for cid in chat_ids:
            subs = self._subs.get(cid)
            if subs is None:
                continue
            subs.discard(connection)
            if not subs:
                self._subs.pop(cid, None)
        self._conn_default.pop(connection, None)
        self._conn_profile.pop(connection, None)
        self._pending_recipe_previews.pop(connection, None)

    @staticmethod
    def _websocket_session_key(profile_id: str, chat_id: str) -> str:
        """Build the canonical websocket session key."""
        pid = (profile_id or "").strip()
        if not pid:
            # Legacy fallback shape kept for compatibility with existing
            # parsing/migration logic when no profile is bound yet.
            return f"websocket:{chat_id}"
        return f"websocket:{pid}:{chat_id}"

    async def _send_event(self, connection: Any, event: str, **fields: Any) -> None:
        """Send a control event (attached, error, ...) to a single connection."""
        payload: dict[str, Any] = {"event": event}
        payload.update(fields)
        raw = json.dumps(payload, ensure_ascii=False)
        try:
            await connection.send(raw)
        except ConnectionClosed:
            self._cleanup_connection(connection)
        except Exception as e:
            logger.warning("websocket: failed to send {} event: {}", event, e)

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return WebSocketConfig().model_dump(by_alias=True)

    def _expected_path(self) -> str:
        return _normalize_config_path(self.config.path)

    def _build_ssl_context(self) -> ssl.SSLContext | None:
        cert = self.config.ssl_certfile.strip()
        key = self.config.ssl_keyfile.strip()
        if not cert and not key:
            return None
        if not cert or not key:
            raise ValueError(
                "websocket: ssl_certfile and ssl_keyfile must both be set for WSS, or both left empty"
            )
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile=cert, keyfile=key)
        return ctx

    _MAX_ISSUED_TOKENS = 10_000

    def _purge_expired_issued_tokens(self) -> None:
        now = time.monotonic()
        for token_key, (expiry, _) in list(self._issued_tokens.items()):
            if now > expiry:
                self._issued_tokens.pop(token_key, None)

    def _take_issued_token_if_valid(self, token_value: str | None) -> str | None:
        """Validate and consume one issued token (single use per connection attempt).

        Uses single-step pop to minimize the window between lookup and removal;
        safe under asyncio's single-threaded cooperative model.
        """
        if not token_value:
            return None
        self._purge_expired_issued_tokens()
        entry = self._issued_tokens.pop(token_value, None)
        if entry is None:
            return None
        expiry, profile_id = entry
        if time.monotonic() > expiry:
            return None
        return normalize_profile_id(profile_id)

    def _handle_token_issue_http(self, connection: Any, request: Any) -> Any:
        secret = self.config.token_issue_secret.strip()
        if secret:
            if not _issue_route_secret_matches(request.headers, secret):
                return connection.respond(401, "Unauthorized")
        else:
            logger.warning(
                "websocket: token_issue_path is set but token_issue_secret is empty; "
                "any client can obtain connection tokens — set token_issue_secret for production."
            )
        self._purge_expired_issued_tokens()
        if len(self._issued_tokens) >= self._MAX_ISSUED_TOKENS:
            logger.error(
                "websocket: too many outstanding issued tokens ({}), rejecting issuance",
                len(self._issued_tokens),
            )
            return _http_json_response({"error": "too many outstanding tokens"}, status=429)
        token_value = f"nbwt_{secrets.token_urlsafe(32)}"
        expiry = time.monotonic() + float(self.config.token_ttl_s)
        self._issued_tokens[token_value] = (expiry, normalize_profile_id(None))

        return _http_json_response(
            {"token": token_value, "expires_in": self.config.token_ttl_s}
        )

    # -- HTTP dispatch ------------------------------------------------------

    async def _dispatch_http(self, connection: Any, request: WsRequest) -> Any:
        """Route an inbound HTTP request to a handler or to the WS upgrade path."""
        got, query = _parse_request_path(request.path)

        # 1. Token issue endpoint (legacy, optional, gated by configured secret).
        if self.config.token_issue_path:
            issue_expected = _normalize_config_path(self.config.token_issue_path)
            if got == issue_expected:
                return self._handle_token_issue_http(connection, request)

        # 2. WebUI bootstrap: localhost-only, mints tokens for the embedded UI.
        if got == "/webui/bootstrap":
            return self._handle_webui_bootstrap(connection, query)

        # 3. REST surface for the embedded UI.
        if got == "/api/profiles":
            return self._handle_profiles_list(request)

        if got == "/api/profiles/create":
            return self._handle_profile_create(request)

        if got == "/api/profiles/delete":
            return self._handle_profile_delete(request)

        if got == "/api/sessions":
            return self._handle_sessions_list(request)

        m = re.match(r"^/api/sessions/([^/]+)/messages$", got)
        if m:
            return self._handle_session_messages(request, m.group(1))

        # NOTE: websockets' HTTP parser only accepts GET, so we cannot expose a
        # true ``DELETE`` verb. The action is folded into the path instead.
        m = re.match(r"^/api/sessions/([^/]+)/delete$", got)
        if m:
            return self._handle_session_delete(request, m.group(1))

        # Signed media fetch: ``<sig>`` is an HMAC over ``<payload>``; the
        # payload decodes to a path inside :func:`get_media_dir`. See
        # :meth:`_sign_media_path` for the inverse direction used to build
        # these URLs when replaying a session.
        m = re.match(r"^/api/media/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)$", got)
        if m:
            return self._handle_media_fetch(m.group(1), m.group(2))

        # 4. WebSocket upgrade (the channel's primary purpose). Only run the
        # handshake gate on requests that actually ask to upgrade; otherwise
        # a bare ``GET /`` from the browser would be rejected as an
        # unauthorized WS handshake instead of serving the SPA's index.html.
        expected_ws = self._expected_path()
        if got == expected_ws and _is_websocket_upgrade(request):
            client_id = _query_first(query, "client_id") or ""
            if len(client_id) > 128:
                client_id = client_id[:128]
            if not self.is_allowed(client_id):
                return connection.respond(403, "Forbidden")
            return self._authorize_websocket_handshake(connection, query)

        # 5. Static SPA serving (only if a build directory was wired in).
        if self._static_dist_path is not None:
            response = self._serve_static(got)
            if response is not None:
                return response

        return connection.respond(404, "Not Found")

    # -- HTTP route handlers ------------------------------------------------

    def _check_api_token(self, request: WsRequest) -> str | None:
        """Validate API token and return the bound profile id."""
        self._purge_expired_api_tokens()
        token = _bearer_token(request.headers) or _query_first(
            _parse_query(request.path), "token"
        )
        if not token:
            return None
        entry = self._api_tokens.get(token)
        if entry is None:
            return None
        expiry, profile_id = entry
        if time.monotonic() > expiry:
            self._api_tokens.pop(token, None)
            return None
        return normalize_profile_id(profile_id)

    def _purge_expired_api_tokens(self) -> None:
        now = time.monotonic()
        for token_key, (expiry, _) in list(self._api_tokens.items()):
            if now > expiry:
                self._api_tokens.pop(token_key, None)

    def _revoke_profile_tokens(self, profile_id: str) -> None:
        pid = (profile_id or "").strip()
        if not pid:
            return
        for token_key, (_, bound_profile) in list(self._issued_tokens.items()):
            if bound_profile == pid:
                self._issued_tokens.pop(token_key, None)
        for token_key, (_, bound_profile) in list(self._api_tokens.items()):
            if bound_profile == pid:
                self._api_tokens.pop(token_key, None)

    def _handle_webui_bootstrap(
        self,
        connection: Any,
        query: dict[str, list[str]],
    ) -> Response:
        if not _is_localhost(connection):
            return _http_error(403, "webui bootstrap is localhost-only")
        # Cap outstanding tokens to avoid runaway growth from a misbehaving client.
        self._purge_expired_issued_tokens()
        self._purge_expired_api_tokens()
        if (
            len(self._issued_tokens) >= self._MAX_ISSUED_TOKENS
            or len(self._api_tokens) >= self._MAX_ISSUED_TOKENS
        ):
            return _http_response(
                json.dumps({"error": "too many outstanding tokens"}).encode("utf-8"),
                status=429,
                content_type="application/json; charset=utf-8",
            )
        profile_id = normalize_profile_id(_query_first(query, "profile_id"))
        token = f"nbwt_{secrets.token_urlsafe(32)}"
        expiry = time.monotonic() + float(self.config.token_ttl_s)
        # Same string registered in both pools: the WS handshake consumes one copy
        # while the REST surface keeps validating the other until TTL expiry.
        self._issued_tokens[token] = (expiry, profile_id)
        self._api_tokens[token] = (expiry, profile_id)
        return _http_json_response(
            {
                "token": token,
                "ws_path": self._expected_path(),
                "expires_in": self.config.token_ttl_s,
                "model_name": _read_webui_model_name(),
                "profile_id": profile_id,
                "profiles": list_profiles(),
            }
        )

    def _handle_sessions_list(self, request: WsRequest) -> Response:
        profile_id = self._check_api_token(request)
        if profile_id is None:
            return _http_error(401, "Unauthorized")
        if self._session_manager is None:
            return _http_error(503, "session manager unavailable")
        sessions = self._session_manager.list_sessions()
        # The webui is only meaningful for websocket-channel chats — CLI /
        # Slack / Lark / Discord sessions can't be resumed from the browser,
        # so leaking them into the sidebar is just noise. Filter to the
        # ``websocket:`` prefix and strip absolute paths on the way out.
        cleaned = [
            {k: v for k, v in s.items() if k != "path"}
            for s in sessions
            if isinstance(s.get("key"), str)
            and self._profile_scoped_webui_key(s["key"], profile_id)
        ]
        return _http_json_response({"sessions": cleaned})

    def _handle_profiles_list(self, request: WsRequest) -> Response:
        profile_id = self._check_api_token(request)
        if profile_id is None:
            return _http_error(401, "Unauthorized")
        return _http_json_response(
            {
                "profiles": list_profiles(),
                "current_profile_id": profile_id,
            }
        )

    def _handle_profile_create(self, request: WsRequest) -> Response:
        if self._check_api_token(request) is None:
            return _http_error(401, "Unauthorized")
        name = (_query_first(_parse_query(request.path), "name") or "").strip()
        if not name:
            return _http_error(400, "name is required")
        try:
            profile = create_profile(name)
        except ValueError as exc:
            return _http_error(400, str(exc))
        return _http_json_response({"profile": {"id": profile.id, "name": profile.name}})

    def _handle_profile_delete(self, request: WsRequest) -> Response:
        current_profile_id = self._check_api_token(request)
        if current_profile_id is None:
            return _http_error(401, "Unauthorized")
        profile_id = (_query_first(_parse_query(request.path), "profile_id") or "").strip()
        if not profile_id:
            return _http_error(400, "profile_id is required")
        if profile_id == current_profile_id:
            return _http_error(400, "cannot delete current profile")
        if not delete_profile(profile_id):
            return _http_error(404, "profile not found")
        self._revoke_profile_tokens(profile_id)
        if self._session_manager is not None:
            self._session_manager.purge_profile_sessions(profile_id)
        return _http_json_response({"deleted": True, "profile_id": profile_id})

    @staticmethod
    def _session_profile_id(key: str) -> str | None:
        """Extract profile id from a websocket session key."""
        if not key.startswith("websocket:"):
            return None
        rest = key[len("websocket:"):]
        if not rest:
            return None
        if ":" not in rest:
            # Legacy two-segment key: websocket:<chat_id>. Keep hidden from
            # profile-scoped webui surfaces.
            return None
        profile_id = rest.split(":", 1)[0].strip()
        return profile_id or None

    @classmethod
    def _profile_scoped_webui_key(cls, key: str, profile_id: str) -> bool:
        """Return True when *key* is websocket-owned and visible to *profile_id*."""
        expected = (profile_id or "").strip()
        if not expected:
            return False
        return cls._session_profile_id(key) == expected

    def _handle_session_messages(self, request: WsRequest, key: str) -> Response:
        profile_id = self._check_api_token(request)
        if profile_id is None:
            return _http_error(401, "Unauthorized")
        if self._session_manager is None:
            return _http_error(503, "session manager unavailable")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        # The embedded webui only understands websocket-channel sessions. Keep
        # its read surface aligned with ``/api/sessions`` instead of letting a
        # caller probe arbitrary CLI / Slack / Lark history by handcrafted URL.
        if not self._profile_scoped_webui_key(decoded_key, profile_id):
            return _http_error(404, "session not found")
        data = self._session_manager.read_session_file(decoded_key)
        if data is None:
            return _http_error(404, "session not found")
        # Decorate persisted user messages with signed media URLs so the
        # client can render previews. The raw on-disk ``media`` paths are
        # stripped on the way out — they leak server filesystem layout and
        # the client never needs them once it has the signed fetch URL.
        self._augment_media_urls(data)
        return _http_json_response(data)

    def _augment_media_urls(self, payload: dict[str, Any]) -> None:
        """Mutate *payload* in place: each message's ``media`` path list is
        replaced by a parallel ``media_urls`` list of signed fetch URLs.

        Messages without media or with non-string path entries are left
        untouched. Paths that no longer live inside ``media_dir`` (e.g. the
        file was deleted, or the dir was relocated) are silently skipped;
        the client falls back to the historical-replay placeholder tile.
        """
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            media = msg.get("media")
            if not isinstance(media, list) or not media:
                continue
            urls: list[dict[str, str]] = []
            for entry in media:
                if not isinstance(entry, str) or not entry:
                    continue
                signed = self._sign_media_path(Path(entry))
                if signed is None:
                    continue
                urls.append({"url": signed, "name": Path(entry).name})
            if urls:
                msg["media_urls"] = urls
            # Always drop the raw paths from the wire payload.
            msg.pop("media", None)

    def _sign_media_path(self, abs_path: Path) -> str | None:
        """Return a ``/api/media/<sig>/<payload>`` URL for *abs_path*, or
        ``None`` when the path does not resolve inside the media root.

        The URL is self-authenticating: the signature binds the payload to
        this process's ``_media_secret``, so only paths we chose to sign can
        be fetched. The returned path is relative to the server origin; the
        client joins it against the existing webui base.
        """
        try:
            media_root = get_media_dir().resolve()
            rel = abs_path.resolve().relative_to(media_root)
        except (OSError, ValueError):
            return None
        payload = _b64url_encode(rel.as_posix().encode("utf-8"))
        mac = hmac.new(
            self._media_secret, payload.encode("ascii"), hashlib.sha256
        ).digest()[:16]
        return f"/api/media/{_b64url_encode(mac)}/{payload}"

    def _handle_media_fetch(self, sig: str, payload: str) -> Response:
        """Serve a single media file previously signed via
        :meth:`_sign_media_path`. Validates the signature, decodes the
        payload to a relative path, and streams the file bytes with a
        long-lived immutable cache header (the URL already encodes the
        file identity, so caches can be aggressive)."""
        try:
            provided_mac = _b64url_decode(sig)
        except (ValueError, binascii.Error):
            return _http_error(401, "invalid signature")
        expected_mac = hmac.new(
            self._media_secret, payload.encode("ascii"), hashlib.sha256
        ).digest()[:16]
        if not hmac.compare_digest(expected_mac, provided_mac):
            return _http_error(401, "invalid signature")
        try:
            rel_bytes = _b64url_decode(payload)
            rel_str = rel_bytes.decode("utf-8")
        except (ValueError, binascii.Error, UnicodeDecodeError):
            return _http_error(400, "invalid payload")
        # An attacker who somehow bypassed the HMAC check would still need
        # the resolved path to escape the media root; guard defensively.
        try:
            media_root = get_media_dir().resolve()
            candidate = (media_root / rel_str).resolve()
            candidate.relative_to(media_root)
        except (OSError, ValueError):
            return _http_error(404, "not found")
        if not candidate.is_file():
            return _http_error(404, "not found")
        try:
            body = candidate.read_bytes()
        except OSError:
            return _http_error(500, "read error")
        mime, _ = mimetypes.guess_type(candidate.name)
        if mime not in _MEDIA_ALLOWED_MIMES:
            mime = "application/octet-stream"
        return _http_response(
            body,
            content_type=mime,
            extra_headers=[
                ("Cache-Control", "private, max-age=31536000, immutable"),
                # Paired with the MIME whitelist above: prevents browsers from
                # MIME-sniffing an octet-stream fallback into executable HTML.
                ("X-Content-Type-Options", "nosniff"),
            ],
        )

    def _handle_session_delete(self, request: WsRequest, key: str) -> Response:
        profile_id = self._check_api_token(request)
        if profile_id is None:
            return _http_error(401, "Unauthorized")
        if self._session_manager is None:
            return _http_error(503, "session manager unavailable")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        # Same boundary as ``_handle_session_messages``: the webui may only
        # mutate websocket sessions, and deletion really does unlink the local
        # JSONL, so keep the blast radius narrow and explicit.
        if not self._profile_scoped_webui_key(decoded_key, profile_id):
            return _http_error(404, "session not found")
        deleted = self._session_manager.delete_session(decoded_key)
        return _http_json_response({"deleted": bool(deleted)})

    def _serve_static(self, request_path: str) -> Response | None:
        """Resolve *request_path* against the built SPA directory; SPA fallback to index.html."""
        assert self._static_dist_path is not None
        rel = request_path.lstrip("/")
        if not rel:
            rel = "index.html"
        # Reject path-traversal attempts and absolute targets.
        if ".." in rel.split("/") or rel.startswith("/"):
            return _http_error(403, "Forbidden")
        candidate = (self._static_dist_path / rel).resolve()
        try:
            candidate.relative_to(self._static_dist_path)
        except ValueError:
            return _http_error(403, "Forbidden")
        if not candidate.is_file():
            # SPA history-mode fallback: unknown routes serve index.html so the
            # client-side router can render them.
            index = self._static_dist_path / "index.html"
            if index.is_file():
                candidate = index
            else:
                return None
        try:
            body = candidate.read_bytes()
        except OSError as e:
            logger.warning("websocket static: failed to read {}: {}", candidate, e)
            return _http_error(500, "Internal Server Error")
        ctype, _ = mimetypes.guess_type(candidate.name)
        if ctype is None:
            ctype = "application/octet-stream"
        if ctype.startswith("text/") or ctype in {"application/javascript", "application/json"}:
            ctype = f"{ctype}; charset=utf-8"
        # Hash-named build assets are cache-friendly; index.html must stay fresh.
        if candidate.name == "index.html":
            cache = "no-cache"
        else:
            cache = "public, max-age=31536000, immutable"
        return _http_response(
            body,
            status=200,
            content_type=ctype,
            extra_headers=[("Cache-Control", cache)],
        )

    @staticmethod
    def _set_connection_profile(connection: Any, profile_id: str) -> None:
        setattr(connection, "_nanobot_profile_id", (profile_id or "").strip())

    @staticmethod
    def _connection_profile(connection: Any) -> str:
        return (getattr(connection, "_nanobot_profile_id", "") or "").strip()

    def _authorize_websocket_handshake(self, connection: Any, query: dict[str, list[str]]) -> Any:
        supplied = _query_first(query, "token")
        static_token = self.config.token.strip()

        if static_token:
            if supplied and hmac.compare_digest(supplied, static_token):
                self._set_connection_profile(connection, normalize_profile_id(None))
                return None
            issued_profile = self._take_issued_token_if_valid(supplied)
            if issued_profile is not None:
                self._set_connection_profile(connection, issued_profile)
                return None
            return connection.respond(401, "Unauthorized")

        if self.config.websocket_requires_token:
            issued_profile = self._take_issued_token_if_valid(supplied)
            if issued_profile is not None:
                self._set_connection_profile(connection, issued_profile)
                return None
            return connection.respond(401, "Unauthorized")

        if supplied:
            issued_profile = self._take_issued_token_if_valid(supplied)
            self._set_connection_profile(
                connection,
                issued_profile if issued_profile is not None else normalize_profile_id(None),
            )
        else:
            self._set_connection_profile(connection, normalize_profile_id(None))
        return None

    async def start(self) -> None:
        self._running = True
        self._stop_event = asyncio.Event()

        ssl_context = self._build_ssl_context()
        scheme = "wss" if ssl_context else "ws"

        async def process_request(
            connection: ServerConnection,
            request: WsRequest,
        ) -> Any:
            return await self._dispatch_http(connection, request)

        async def handler(connection: ServerConnection) -> None:
            await self._connection_loop(connection)

        logger.info(
            "WebSocket server listening on {}://{}:{}{}",
            scheme,
            self.config.host,
            self.config.port,
            self.config.path,
        )
        if self.config.token_issue_path:
            logger.info(
                "WebSocket token issue route: {}://{}:{}{}",
                scheme,
                self.config.host,
                self.config.port,
                _normalize_config_path(self.config.token_issue_path),
            )

        async def runner() -> None:
            async with serve(
                handler,
                self.config.host,
                self.config.port,
                process_request=process_request,
                max_size=self.config.max_message_bytes,
                ping_interval=self.config.ping_interval_s,
                ping_timeout=self.config.ping_timeout_s,
                ssl=ssl_context,
            ):
                assert self._stop_event is not None
                await self._stop_event.wait()

        self._server_task = asyncio.create_task(runner())
        await self._server_task

    async def _connection_loop(self, connection: Any) -> None:
        request = connection.request
        path_part = request.path if request else "/"
        _, query = _parse_request_path(path_part)
        client_id_raw = _query_first(query, "client_id")
        client_id = client_id_raw.strip() if client_id_raw else ""
        if not client_id:
            client_id = f"anon-{uuid.uuid4().hex[:12]}"
        elif len(client_id) > 128:
            logger.warning("websocket: client_id too long ({} chars), truncating", len(client_id))
            client_id = client_id[:128]

        default_chat_id = str(uuid.uuid4())
        profile_id = self._connection_profile(connection)

        try:
            await connection.send(
                json.dumps(
                    {
                        "event": "ready",
                        "chat_id": default_chat_id,
                        "client_id": client_id,
                    },
                    ensure_ascii=False,
                )
            )
            # Register only after ready is successfully sent to avoid out-of-order sends
            self._conn_default[connection] = default_chat_id
            self._conn_profile[connection] = profile_id
            self._attach(connection, default_chat_id)

            async for raw in connection:
                if isinstance(raw, bytes):
                    try:
                        raw = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        logger.warning("websocket: ignoring non-utf8 binary frame")
                        continue

                envelope = _parse_envelope(raw)
                if envelope is not None:
                    await self._dispatch_envelope(connection, client_id, envelope)
                    continue

                content = _parse_inbound_payload(raw)
                if content is None:
                    continue
                await self._handle_message(
                    sender_id=client_id,
                    chat_id=default_chat_id,
                    content=content,
                    metadata={"remote": getattr(connection, "remote_address", None)},
                    session_key=self._websocket_session_key(profile_id, default_chat_id),
                )
        except Exception as e:
            logger.debug("websocket connection ended: {}", e)
        finally:
            self._cleanup_connection(connection)

    @staticmethod
    def _save_envelope_media(
        media: list[Any],
    ) -> tuple[list[str], str | None]:
        """Decode and persist ``media`` items from a ``message`` envelope.

        Returns ``(paths, None)`` on success or ``([], reason)`` on the first
        failure — the caller is expected to surface ``reason`` to the client
        and skip publishing so no half-formed message ever reaches the agent.
        On failure, any images already written to disk earlier in the same
        call are unlinked so partial ingress doesn't leak orphan files.
        ``reason`` is a short, stable token suitable for UI localization.

        Shape: ``list[{"data_url": str, "name"?: str | None}]``.
        """
        if len(media) > _MAX_IMAGES_PER_MESSAGE:
            return [], "too_many_images"
        media_dir = get_media_dir("websocket")
        paths: list[str] = []

        def _abort(reason: str) -> tuple[list[str], str]:
            for p in paths:
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning(
                        "websocket: failed to unlink partial media {}: {}", p, exc
                    )
            return [], reason

        for item in media:
            if not isinstance(item, dict):
                return _abort("malformed")
            data_url = item.get("data_url")
            if not isinstance(data_url, str) or not data_url:
                return _abort("malformed")
            mime = _extract_data_url_mime(data_url)
            if mime is None:
                return _abort("decode")
            if mime not in _IMAGE_MIME_ALLOWED:
                return _abort("mime")
            try:
                saved = save_base64_data_url(
                    data_url, media_dir, max_bytes=_MAX_IMAGE_BYTES,
                )
            except FileSizeExceeded:
                return _abort("size")
            except Exception as exc:
                logger.warning("websocket: media decode failed: {}", exc)
                return _abort("decode")
            if saved is None:
                return _abort("decode")
            paths.append(saved)
        return paths, None

    async def _dispatch_envelope(
        self,
        connection: Any,
        client_id: str,
        envelope: dict[str, Any],
    ) -> None:
        """Route one typed inbound envelope (``new_chat`` / ``attach`` / ``message``)."""
        t = envelope.get("type")
        profile_id = (self._conn_profile.get(connection, "") or "").strip()

        if t == "model_list":
            if not profile_id:
                await self._send_event(connection, "error", detail="profile_required")
                return
            try:
                user_models = self._model_store.list_models(profile_id)
            except ValueError:
                await self._send_event(connection, "error", detail="profile_required")
                return
            models = [resolve_global_base_model()] + [
                {**m, "readonly": False, "source": "user_custom"}
                for m in user_models
            ]
            await self._send_event(connection, "model_list", models=models)
            return

        if t == "model_add":
            if not profile_id:
                await self._send_event(connection, "error", detail="profile_required")
                return
            name = envelope.get("name")
            model_name = envelope.get("model_name")
            base_url = envelope.get("base_url")
            api_key = envelope.get("api_key")
            supports_thinking = bool(envelope.get("supports_thinking", False))
            thinking_doc_url = envelope.get("thinking_doc_url")
            thinking_snippet = envelope.get("thinking_snippet")
            if not all(isinstance(v, str) for v in (name, model_name, base_url, api_key)):
                await self._send_event(connection, "error", detail="invalid_model_payload")
                return
            if thinking_doc_url is not None and not isinstance(thinking_doc_url, str):
                await self._send_event(connection, "error", detail="invalid_model_payload")
                return
            if thinking_snippet is not None and not isinstance(thinking_snippet, str):
                await self._send_event(connection, "error", detail="invalid_model_payload")
                return
            if supports_thinking and not (str(thinking_doc_url or "").strip() or str(thinking_snippet or "").strip()):
                await self._send_event(connection, "error", detail="thinking_source_required")
                return
            try:
                row = self._model_store.add_model(
                    profile_id,
                    name=name,
                    model_name=model_name,
                    base_url=base_url,
                    api_key=api_key,
                    supports_thinking=supports_thinking,
                )
                if supports_thinking:
                    compiled = self._thinking_recipe_store.compile(
                        model_name=model_name,
                        base_url=base_url,
                        doc_url=thinking_doc_url,
                        snippet=thinking_snippet,
                    )
                    self._thinking_recipe_store.save(
                        recipe=compiled.get("recipe", {}),
                        evidence=compiled.get("evidence", []),
                    )
            except ValueError as e:
                await self._send_event(connection, "error", detail=str(e))
                return
            await self._send_event(
                connection,
                "model_saved",
                model={**row, "readonly": False, "source": "user_custom"},
            )
            return

        if t == "model_delete":
            if not profile_id:
                await self._send_event(connection, "error", detail="profile_required")
                return
            model_id = envelope.get("model_id")
            if not isinstance(model_id, str) or not model_id.strip():
                await self._send_event(connection, "error", detail="invalid_model_id")
                return
            if model_id == GLOBAL_BASE_MODEL_ID:
                await self._send_event(connection, "error", detail="forbidden_global_model")
                return
            deleted = self._model_store.delete_model(profile_id, model_id)
            if not deleted:
                await self._send_event(connection, "error", detail="model_not_found")
                return
            await self._send_event(connection, "model_deleted", model_id=model_id)
            return

        if t == "model_select":
            if not profile_id:
                await self._send_event(connection, "error", detail="profile_required")
                return
            cid = envelope.get("chat_id")
            if not _is_valid_chat_id(cid):
                await self._send_event(connection, "error", detail="invalid chat_id")
                return
            raw_model_id = envelope.get("model_id")
            model_id = raw_model_id if isinstance(raw_model_id, str) else None
            selected_model_id: str | None = None
            selected_model_name: str | None = None
            selected_supports_thinking: bool | None = None
            if model_id and model_id != GLOBAL_BASE_MODEL_ID:
                row = self._model_store.get_model(profile_id, model_id)
                if row is not None:
                    selected_model_id = row["id"]
                    selected_model_name = row["name"]
                    selected_supports_thinking = bool(row.get("supports_thinking", False))
            self._set_session_model_selection(
                profile_id,
                cid,
                selected_model_id=selected_model_id,
                selected_model_name=selected_model_name,
                selected_model_supports_thinking=selected_supports_thinking,
            )
            thinking_status = self._resolve_session_thinking_status(
                profile_id=profile_id,
                chat_id=cid,
            )
            await self._send_event(
                connection,
                "model_selected",
                chat_id=cid,
                selected_model_id=selected_model_id,
                selected_model_name=selected_model_name,
                selected_model_supports_thinking=selected_supports_thinking,
                thinking_supported=thinking_status["supported"],
                thinking_recipe_ready=thinking_status["recipe_ready"],
                thinking_unavailable_reason=thinking_status["reason"],
            )
            return

        if t == "new_chat":
            new_id = str(uuid.uuid4())
            self._attach(connection, new_id)
            selected = self._get_session_model_selection(profile_id, new_id)
            thinking_status = self._resolve_session_thinking_status(
                profile_id=profile_id,
                chat_id=new_id,
            )
            await self._send_event(
                connection,
                "attached",
                chat_id=new_id,
                selected_model_id=selected.get("selected_model_id"),
                selected_model_name=selected.get("selected_model_name"),
                selected_model_supports_thinking=selected.get("selected_model_supports_thinking"),
                thinking_supported=thinking_status["supported"],
                thinking_recipe_ready=thinking_status["recipe_ready"],
                thinking_unavailable_reason=thinking_status["reason"],
            )
            return
        if t == "attach":
            cid = envelope.get("chat_id")
            if not _is_valid_chat_id(cid):
                await self._send_event(connection, "error", detail="invalid chat_id")
                return
            self._attach(connection, cid)
            selected = self._get_session_model_selection(profile_id, cid)
            thinking_status = self._resolve_session_thinking_status(
                profile_id=profile_id,
                chat_id=cid,
            )
            await self._send_event(
                connection,
                "attached",
                chat_id=cid,
                selected_model_id=selected.get("selected_model_id"),
                selected_model_name=selected.get("selected_model_name"),
                selected_model_supports_thinking=selected.get("selected_model_supports_thinking"),
                thinking_supported=thinking_status["supported"],
                thinking_recipe_ready=thinking_status["recipe_ready"],
                thinking_unavailable_reason=thinking_status["reason"],
            )
            return
        if t == "thinking_toggle":
            cid = envelope.get("chat_id")
            enabled = envelope.get("enabled")
            if not _is_valid_chat_id(cid):
                await self._send_event(connection, "error", detail="invalid chat_id")
                return
            if not isinstance(enabled, bool):
                await self._send_event(connection, "error", detail="invalid_thinking_toggle")
                return
            self._attach(connection, cid)
            if enabled:
                model = self._resolve_model_context(
                    profile_id=profile_id,
                    chat_id=cid,
                    explicit_model_id=envelope.get("model_id"),
                )
                allowed, reason = self._check_thinking_gate(model)
                if not allowed and reason == "model_does_not_support_thinking":
                    await self._send_event(
                        connection,
                        "thinking_recipe_error",
                        chat_id=cid,
                        detail=reason,
                    )
                    return
                if not allowed:
                    await self._send_event(
                        connection,
                        "thinking_recipe_required",
                        chat_id=cid,
                        reason=reason or "missing_recipe",
                    )
            return
        if t == "thinking_recipe_submit":
            cid = envelope.get("chat_id")
            if not _is_valid_chat_id(cid):
                await self._send_event(connection, "error", detail="invalid chat_id")
                return
            self._attach(connection, cid)
            model = self._resolve_model_context(
                profile_id=profile_id,
                chat_id=cid,
                explicit_model_id=envelope.get("model_id"),
            )
            if not bool(model.get("supports_thinking", False)):
                await self._send_event(
                    connection,
                    "thinking_recipe_error",
                    chat_id=cid,
                    detail="model_does_not_support_thinking",
                )
                return
            doc_url = envelope.get("doc_url")
            snippet = envelope.get("snippet")
            if doc_url is not None and not isinstance(doc_url, str):
                await self._send_event(
                    connection,
                    "thinking_recipe_error",
                    chat_id=cid,
                    detail="invalid_doc_url",
                )
                return
            if snippet is not None and not isinstance(snippet, str):
                await self._send_event(
                    connection,
                    "thinking_recipe_error",
                    chat_id=cid,
                    detail="invalid_snippet",
                )
                return
            async def _emit_recipe_progress(payload: dict[str, Any]) -> None:
                await self._send_event(
                    connection,
                    "thinking_recipe_progress",
                    chat_id=cid,
                    stage=str(payload.get("stage") or ""),
                    message=payload.get("message"),
                    meta=payload.get("meta"),
                )

            try:
                compiled = await self._thinking_recipe_orchestrator.submit(
                    model_name=str(model.get("model_name") or ""),
                    base_url=str(model.get("base_url") or ""),
                    doc_url=doc_url,
                    snippet=snippet,
                    on_progress=_emit_recipe_progress,
                )
            except ValueError as e:
                await self._send_event(
                    connection,
                    "thinking_recipe_error",
                    chat_id=cid,
                    detail=str(e),
                )
                return

            orch_meta = compiled.get("orchestrator")
            if isinstance(orch_meta, dict) and bool(orch_meta.get("conflict")):
                detail = orch_meta.get("conflict_detail")
                if isinstance(detail, dict):
                    await self._send_event(
                        connection,
                        "thinking_recipe_conflict_detected",
                        chat_id=cid,
                        model_name=model.get("model_name"),
                        base_url=model.get("base_url"),
                        decision=detail.get("decision"),
                        scoring=detail.get("scoring"),
                        evidence=detail.get("evidence"),
                    )

            preview_id = str(compiled.get("preview_id") or "")
            pending = self._pending_recipe_previews.setdefault(connection, {})
            pending[preview_id] = {
                "recipe": compiled.get("recipe", {}),
                "evidence": compiled.get("evidence", []),
            }
            await self._send_event(
                connection,
                "thinking_recipe_preview",
                chat_id=cid,
                preview_id=preview_id,
                model_name=model.get("model_name"),
                base_url=model.get("base_url"),
                recipe=compiled.get("recipe"),
                evidence=compiled.get("evidence"),
            )
            return
        if t == "thinking_recipe_confirm":
            cid = envelope.get("chat_id")
            if not _is_valid_chat_id(cid):
                await self._send_event(connection, "error", detail="invalid chat_id")
                return
            self._attach(connection, cid)
            preview_id = envelope.get("preview_id")
            if not isinstance(preview_id, str) or not preview_id.strip():
                await self._send_event(
                    connection,
                    "thinking_recipe_error",
                    chat_id=cid,
                    detail="invalid_preview_id",
                )
                return
            pending = self._pending_recipe_previews.get(connection, {})
            preview = pending.get(preview_id)
            if preview is None:
                await self._send_event(
                    connection,
                    "thinking_recipe_error",
                    chat_id=cid,
                    detail="preview_not_found",
                )
                return
            try:
                saved = self._thinking_recipe_store.save(
                    recipe=preview.get("recipe", {}),
                    evidence=preview.get("evidence", []),
                )
            except ValueError as e:
                await self._send_event(
                    connection,
                    "thinking_recipe_error",
                    chat_id=cid,
                    detail=str(e),
                )
                return
            pending.pop(preview_id, None)
            await self._send_event(
                connection,
                "thinking_recipe_saved",
                chat_id=cid,
                model_signature=saved.get("model_signature"),
                model_name=saved.get("model_name"),
                base_url=saved.get("base_url"),
                updated_at=saved.get("updated_at"),
                created=saved.get("created"),
            )
            return
        if t == "message":
            cid = envelope.get("chat_id")
            content = envelope.get("content")
            if not _is_valid_chat_id(cid):
                await self._send_event(connection, "error", detail="invalid chat_id")
                return
            if not isinstance(content, str):
                await self._send_event(connection, "error", detail="missing content")
                return

            raw_media = envelope.get("media")
            media_paths: list[str] = []
            if raw_media is not None:
                if not isinstance(raw_media, list):
                    await self._send_event(
                        connection, "error",
                        detail="image_rejected", reason="malformed",
                    )
                    return
                media_paths, reason = self._save_envelope_media(raw_media)
                if reason is not None:
                    await self._send_event(
                        connection, "error",
                        detail="image_rejected", reason=reason,
                    )
                    return

            # Allow image-only turns (content may be empty when media is attached).
            if not content.strip() and not media_paths:
                await self._send_event(connection, "error", detail="missing content")
                return

            # Auto-attach on first use so clients can one-shot without a separate attach.
            self._attach(connection, cid)

            # Parse optional thinking/reasoning_effort from the envelope.
            metadata: dict[str, Any] = {"remote": getattr(connection, "remote_address", None)}
            explicit_model_id = envelope.get("model_id")
            chosen_model_id: str | None = None
            if isinstance(explicit_model_id, str):
                if explicit_model_id and explicit_model_id != GLOBAL_BASE_MODEL_ID and profile_id:
                    row = self._model_store.get_model(profile_id, explicit_model_id)
                    if row is not None:
                        chosen_model_id = row["id"]
                        metadata["_model_id"] = chosen_model_id
                        metadata["_selected_model_supports_thinking"] = bool(
                            row.get("supports_thinking", False)
                        )
                        self._set_session_model_selection(
                            profile_id,
                            cid,
                            selected_model_id=row["id"],
                            selected_model_name=row["name"],
                            selected_model_supports_thinking=bool(
                                row.get("supports_thinking", False)
                            ),
                        )
                    else:
                        self._set_session_model_selection(
                            profile_id,
                            cid,
                            selected_model_id=None,
                            selected_model_name=None,
                            selected_model_supports_thinking=None,
                        )
                else:
                    self._set_session_model_selection(
                        profile_id,
                        cid,
                        selected_model_id=None,
                        selected_model_name=None,
                        selected_model_supports_thinking=None,
                    )
            else:
                selected = self._get_session_model_selection(profile_id, cid)
                sid = selected.get("selected_model_id")
                if isinstance(sid, str) and sid and profile_id:
                    row = self._model_store.get_model(profile_id, sid)
                    if row is not None:
                        chosen_model_id = sid
                        metadata["_model_id"] = sid
                        metadata["_selected_model_supports_thinking"] = bool(
                            row.get("supports_thinking", False)
                        )
                    else:
                        self._set_session_model_selection(
                            profile_id,
                            cid,
                            selected_model_id=None,
                            selected_model_name=None,
                            selected_model_supports_thinking=None,
                        )
            thinking = envelope.get("thinking")
            if isinstance(thinking, dict):
                thinking_enabled = thinking.get("enabled", False)
                if thinking_enabled:
                    model = self._resolve_model_context(
                        profile_id=profile_id,
                        chat_id=cid,
                        explicit_model_id=chosen_model_id,
                    )
                    allowed, reason = self._check_thinking_gate(model)
                    if not allowed:
                        if reason == "model_does_not_support_thinking":
                            await self._send_event(
                                connection,
                                "thinking_recipe_error",
                                chat_id=cid,
                                detail=reason,
                            )
                        else:
                            await self._send_event(
                                connection,
                                "thinking_recipe_required",
                                chat_id=cid,
                                reason=reason or "missing_recipe",
                            )
                    if allowed:
                        metadata["_thinking_enabled"] = True
                        effort = thinking.get("effort")
                        # Default to "high" if thinking is enabled but no effort specified
                        metadata["_reasoning_effort"] = effort if effort in ("high", "max") else "high"

            if chosen_model_id:
                metadata["_model_id"] = chosen_model_id

            await self._handle_message(
                sender_id=client_id,
                chat_id=cid,
                content=content,
                media=media_paths or None,
                metadata=metadata,
                session_key=self._websocket_session_key(
                    self._conn_profile.get(connection, ""),
                    cid,
                ),
            )
            return
        await self._send_event(connection, "error", detail=f"unknown type: {t!r}")

    def _resolve_model_context(
        self,
        *,
        profile_id: str,
        chat_id: str,
        explicit_model_id: Any = None,
    ) -> dict[str, Any]:
        """Resolve model descriptor for model-scoped operations."""
        if isinstance(explicit_model_id, str):
            model_id = explicit_model_id.strip()
            if model_id and model_id != GLOBAL_BASE_MODEL_ID and profile_id:
                row = self._model_store.get_model(profile_id, model_id)
                if row is not None:
                    return {
                        "id": row.get("id"),
                        "name": row.get("name"),
                        "model_name": row.get("model_name"),
                        "base_url": row.get("base_url"),
                        "supports_thinking": bool(row.get("supports_thinking", False)),
                        "source": "user_custom",
                    }
        selected = self._get_session_model_selection(profile_id, chat_id)
        selected_id = selected.get("selected_model_id")
        if isinstance(selected_id, str) and selected_id and profile_id:
            row = self._model_store.get_model(profile_id, selected_id)
            if row is not None:
                return {
                    "id": row.get("id"),
                    "name": row.get("name"),
                    "model_name": row.get("model_name"),
                    "base_url": row.get("base_url"),
                    "supports_thinking": bool(row.get("supports_thinking", False)),
                    "source": "user_custom",
                }
        return resolve_global_base_model()

    @staticmethod
    def _recipe_validated(recipe: dict[str, Any] | None) -> bool:
        if not isinstance(recipe, dict):
            return False
        return bool(recipe.get("validated") is True)

    def _check_thinking_gate(self, model: dict[str, Any]) -> tuple[bool, str | None]:
        """Evaluate the 4E gate: support + global recipe exists + validated.

        Returns ``(allowed, reason)`` where ``reason`` is one of:
        - ``model_does_not_support_thinking``
        - ``missing_recipe``
        - ``unvalidated_recipe``
        """
        if not bool(model.get("supports_thinking", False)):
            return False, "model_does_not_support_thinking"
        if str(model.get("source") or "") == "global_base":
            # Base model keeps current provider behavior and should not depend on
            # user-managed recipe documents.
            return True, None
        recipe = self._thinking_recipe_store.lookup(
            model_name=str(model.get("model_name") or ""),
            base_url=str(model.get("base_url") or ""),
        )
        if recipe is None:
            return False, "missing_recipe"
        if not self._recipe_validated(recipe):
            return False, "unvalidated_recipe"
        return True, None

    def _resolve_session_thinking_status(
        self,
        *,
        profile_id: str,
        chat_id: str,
    ) -> dict[str, Any]:
        model = self._resolve_model_context(
            profile_id=profile_id,
            chat_id=chat_id,
            explicit_model_id=None,
        )
        allowed, reason = self._check_thinking_gate(model)
        supported = bool(model.get("supports_thinking", False))
        return {
            "supported": supported,
            "recipe_ready": bool(allowed),
            "reason": None if allowed else (reason or "missing_recipe"),
        }

    def _session_model_key(self, profile_id: str, chat_id: str) -> str:
        return self._websocket_session_key(profile_id, chat_id)

    def _get_session_model_selection(self, profile_id: str, chat_id: str) -> dict[str, Any]:
        if not self._session_manager or not profile_id:
            return {}
        key = self._session_model_key(profile_id, chat_id)
        session = self._session_manager.get_or_create(key)
        meta = session.metadata if isinstance(session.metadata, dict) else {}
        selected_model_id = meta.get("selected_model_id")
        if isinstance(selected_model_id, str) and selected_model_id:
            row = self._model_store.get_model(profile_id, selected_model_id)
            if row is None:
                # Selected custom model was deleted or became invalid.
                self._set_session_model_selection(
                    profile_id,
                    chat_id,
                    selected_model_id=None,
                    selected_model_name=None,
                    selected_model_supports_thinking=None,
                )
                return {}
        return {
            "selected_model_id": meta.get("selected_model_id"),
            "selected_model_name": meta.get("selected_model_name"),
            "selected_model_supports_thinking": meta.get("selected_model_supports_thinking"),
        }

    def _set_session_model_selection(
        self,
        profile_id: str,
        chat_id: str,
        *,
        selected_model_id: str | None,
        selected_model_name: str | None,
        selected_model_supports_thinking: bool | None,
    ) -> None:
        if not self._session_manager or not profile_id:
            return
        key = self._session_model_key(profile_id, chat_id)
        session = self._session_manager.get_or_create(key)
        if selected_model_id:
            session.metadata["selected_model_id"] = selected_model_id
            session.metadata["selected_model_name"] = selected_model_name or selected_model_id
            session.metadata["selected_model_supports_thinking"] = bool(
                selected_model_supports_thinking
            )
        else:
            session.metadata.pop("selected_model_id", None)
            session.metadata.pop("selected_model_name", None)
            session.metadata.pop("selected_model_supports_thinking", None)
        self._session_manager.save(session)

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._stop_event:
            self._stop_event.set()
        if self._server_task:
            try:
                await self._server_task
            except Exception as e:
                logger.warning("websocket: server task error during shutdown: {}", e)
            self._server_task = None
        self._subs.clear()
        self._conn_chats.clear()
        self._conn_default.clear()
        self._conn_profile.clear()
        self._pending_recipe_previews.clear()
        self._issued_tokens.clear()
        self._api_tokens.clear()

    async def _safe_send_to(self, connection: Any, raw: str, *, label: str = "") -> None:
        """Send a raw frame to one connection, cleaning up on ConnectionClosed."""
        try:
            await connection.send(raw)
        except ConnectionClosed:
            self._cleanup_connection(connection)
            logger.warning("websocket{}connection gone", label)
        except Exception as e:
            logger.error("websocket{}send failed: {}", label, e)
            raise

    async def send(self, msg: OutboundMessage) -> None:
        # Snapshot the subscriber set so ConnectionClosed cleanups mid-iteration are safe.
        conns = list(self._subs.get(msg.chat_id, ()))
        if not conns:
            logger.warning("websocket: no active subscribers for chat_id={}", msg.chat_id)
            return
        payload: dict[str, Any] = {
            "event": "message",
            "chat_id": msg.chat_id,
            "text": msg.content,
        }
        if msg.media:
            payload["media"] = msg.media
        if msg.reply_to:
            payload["reply_to"] = msg.reply_to
        # Mark intermediate agent breadcrumbs (tool-call hints, generic
        # progress strings) so WS clients can render them as subordinate
        # trace rows rather than conversational replies.
        if msg.metadata.get("_tool_hint"):
            payload["kind"] = "tool_hint"
        elif msg.metadata.get("_progress"):
            payload["kind"] = "progress"
        raw = json.dumps(payload, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" ")

    async def send_reasoning_delta(self, chat_id: str, reasoning: str) -> None:
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body = {
            "event": "reasoning_delta",
            "chat_id": chat_id,
            "text": reasoning,
        }
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" reasoning ")

    async def send_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        meta = metadata or {}
        if meta.get("_stream_end"):
            body: dict[str, Any] = {"event": "stream_end", "chat_id": chat_id}
        else:
            body = {
                "event": "delta",
                "chat_id": chat_id,
                "text": delta,
            }
        if meta.get("_stream_id") is not None:
            body["stream_id"] = meta["_stream_id"]
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" stream ")
