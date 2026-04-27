from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus


def _make_loop(workspace: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "base-model"
    provider.generation = SimpleNamespace(
        max_tokens=2048,
        temperature=0.1,
        reasoning_effort=None,
    )
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        model="base-model",
    )


def _stub_run_result(loop: AgentLoop) -> None:
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop._run_agent_loop = AsyncMock(return_value=(  # type: ignore[method-assign]
        "ok",
        [],
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "ok"},
        ],
        "stop",
        False,
    ))


def test_process_message_uses_custom_model_runner_and_disables_reasoning_when_not_supported(
    tmp_path: Path,
) -> None:
    loop = _make_loop(tmp_path)
    _stub_run_result(loop)
    saved = loop._model_store.add_model(
        "alice",
        name="custom-a",
        model_name="custom-model",
        base_url="https://example.com/v1",
        api_key="sk-test",
        supports_thinking=False,
    )

    result = asyncio.run(
        loop._process_message(
            InboundMessage(
                channel="websocket",
                sender_id="u1",
                chat_id="alice:chat-1",
                content="hello",
                metadata={"_model_id": saved["id"], "_reasoning_effort": "high"},
            )
        )
    )

    assert result is not None
    kwargs = loop._run_agent_loop.await_args.kwargs
    assert kwargs["model_override"] == "custom-model"
    assert kwargs["runner_override"] is not None
    assert kwargs["reasoning_effort"] is None


def test_process_message_falls_back_to_base_model_when_model_id_is_invalid(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    _stub_run_result(loop)

    result = asyncio.run(
        loop._process_message(
            InboundMessage(
                channel="websocket",
                sender_id="u1",
                chat_id="alice:chat-1",
                content="hello",
                metadata={"_model_id": "missing-id", "_reasoning_effort": "medium"},
            )
        )
    )

    assert result is not None
    kwargs = loop._run_agent_loop.await_args.kwargs
    assert kwargs["runner_override"] is None
    assert kwargs["model_override"] is None
    assert kwargs["reasoning_effort"] == "medium"


def test_process_message_falls_back_when_selected_model_has_missing_api_key(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    _stub_run_result(loop)
    saved = loop._model_store.add_model(
        "alice",
        name="broken",
        model_name="broken-model",
        base_url="https://example.com/v1",
        api_key="",
        supports_thinking=False,
    )

    result = asyncio.run(
        loop._process_message(
            InboundMessage(
                channel="websocket",
                sender_id="u1",
                chat_id="alice:chat-1",
                content="hello",
                metadata={"_model_id": saved["id"], "_reasoning_effort": "medium"},
            )
        )
    )

    assert result is not None
    kwargs = loop._run_agent_loop.await_args.kwargs
    assert kwargs["runner_override"] is None
    assert kwargs["model_override"] is None
    assert kwargs["reasoning_effort"] == "medium"


def test_process_message_falls_back_when_model_store_read_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    loop = _make_loop(tmp_path)
    _stub_run_result(loop)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("read error")

    monkeypatch.setattr(loop._model_store, "get_model", _boom)
    result = asyncio.run(
        loop._process_message(
            InboundMessage(
                channel="websocket",
                sender_id="u1",
                chat_id="alice:chat-1",
                content="hello",
                metadata={"_model_id": "any", "_reasoning_effort": "low"},
            )
        )
    )

    assert result is not None
    kwargs = loop._run_agent_loop.await_args.kwargs
    assert kwargs["runner_override"] is None
    assert kwargs["model_override"] is None
    assert kwargs["reasoning_effort"] == "low"
