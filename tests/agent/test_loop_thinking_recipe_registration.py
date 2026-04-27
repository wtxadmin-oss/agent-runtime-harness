from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from nanobot.agent.loop import AgentLoop
from nanobot.agent.subagent import SubagentManager, SubagentStatus
from nanobot.bus.queue import MessageBus


def test_agent_loop_registers_thinking_recipe_tools(tmp_path) -> None:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")

    assert "thinking_recipe_lookup" in loop.tools.tool_names
    assert "thinking_recipe_compile" in loop.tools.tool_names


def test_subagent_registers_thinking_recipe_tools(tmp_path) -> None:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=4096,
    )
    captured: dict[str, list[str]] = {}

    async def fake_run(spec):
        captured["tool_names"] = spec.tools.tool_names
        return SimpleNamespace(
            stop_reason="ok",
            final_content="done",
            tool_events=[],
            error=None,
        )

    mgr.runner.run = fake_run
    mgr._announce_result = AsyncMock()
    status = SubagentStatus(
        task_id="sub-1",
        label="label",
        task_description="task",
        started_at=0.0,
    )
    asyncio.run(
        mgr._run_subagent(
            "sub-1",
            "task",
            "label",
            {"channel": "cli", "chat_id": "direct"},
            status,
        )
    )

    assert "thinking_recipe_lookup" in captured["tool_names"]
    assert "thinking_recipe_compile" in captured["tool_names"]
