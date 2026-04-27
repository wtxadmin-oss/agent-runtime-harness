from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus


class _StubProvider:
    def __init__(self) -> None:
        self.generation = SimpleNamespace(
            max_tokens=2048,
            temperature=0.1,
            reasoning_effort=None,
        )

    def get_default_model(self) -> str:
        return "test-model"


def _make_loop(workspace: Path) -> AgentLoop:
    return AgentLoop(
        bus=MessageBus(),
        provider=_StubProvider(),
        workspace=workspace,
        model="test-model",
    )


def test_memory_store_for_websocket_profile_uses_profile_workspace(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)

    store = loop._memory_store_for_session_key("websocket:alice:chat-1")
    assert store.workspace == tmp_path / "users" / "alice"
    assert store.memory_file == tmp_path / "users" / "alice" / "memory" / "MEMORY.md"


def test_memory_store_for_non_profile_session_uses_root_workspace(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)

    store = loop._memory_store_for_session_key("cli:default")
    assert store.workspace == tmp_path
    assert store.memory_file == tmp_path / "memory" / "MEMORY.md"
