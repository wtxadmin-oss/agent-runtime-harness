from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from nanobot.profile.thinking_recipe_orchestrator import (
    CodeExtractorSubagent,
    CompareSubagent,
    ExtractionResult,
    ThinkingRecipeOrchestrator,
    UrlExtractorSubagent,
)


def test_runtime_registers_three_subagents(tmp_path: Path) -> None:
    orch = ThinkingRecipeOrchestrator(workspace=tmp_path)
    assert orch.registered_subagents == [
        "code_extractor_subagent",
        "compare_subagent",
        "url_extractor_subagent",
    ]


def test_submit_snippet_only_uses_code_subagent(tmp_path: Path) -> None:
    orch = ThinkingRecipeOrchestrator(workspace=tmp_path)
    progress: list[str] = []

    async def _on_progress(payload: dict[str, object]) -> None:
        progress.append(str(payload.get("stage") or ""))

    result = asyncio.run(
        orch.submit(
            model_name="qwen3-plus",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            doc_url=None,
            snippet='{"thinking":{"enabled":true},"reasoning_effort":"high"}',
            on_progress=_on_progress,
        )
    )
    meta = result.get("orchestrator") or {}
    assert meta.get("mode") == "single"
    assert meta.get("used_subagents") == [CodeExtractorSubagent.name]
    assert meta.get("conflict") is False
    assert (result.get("recipe") or {}).get("validated") is True
    assert progress == ["submitted", "extracting", "compiling", "preview_ready"]


def test_submit_dual_conflict_triggers_compare_subagent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    orch = ThinkingRecipeOrchestrator(workspace=tmp_path)

    class _FakeCode:
        name = CodeExtractorSubagent.name

        def run(self, **kwargs):
            return ExtractionResult(
                source_type="snippet",
                candidates={
                    "enabled_param": "thinking.enabled",
                    "effort_param": "reasoning_effort",
                    "allowed_efforts": ["high", "max"],
                },
                evidence=[{"kind": "snippet", "source": "inline", "quote": "thinking.enabled"}],
                confidence=0.8,
                ok=True,
            )

    class _FakeUrl:
        name = UrlExtractorSubagent.name

        def run(self, **kwargs):
            return ExtractionResult(
                source_type="url",
                candidates={
                    "enabled_param": "enable_thinking",
                    "effort_param": "thinking.effort",
                    "allowed_efforts": ["medium", "high"],
                },
                evidence=[{"kind": "url", "source": "https://docs", "quote": "enable_thinking"}],
                confidence=0.85,
                ok=True,
            )

    seen: dict[str, int] = {"compare": 0}

    class _FakeCompare:
        name = CompareSubagent.name

        def run(self, **kwargs):
            seen["compare"] += 1
            return {
                "source_type": "merged",
                "decision": "url",
                "scoring": {"url": {}, "snippet": {}},
                "evidence": [],
                "candidates": {
                    "enabled_param": "enable_thinking",
                    "effort_param": "thinking.effort",
                    "allowed_efforts": ["high", "max"],
                },
            }

    monkeypatch.setitem(orch._subagents, CodeExtractorSubagent.name, _FakeCode())
    monkeypatch.setitem(orch._subagents, UrlExtractorSubagent.name, _FakeUrl())
    monkeypatch.setitem(orch._subagents, CompareSubagent.name, _FakeCompare())

    out = asyncio.run(
        orch.submit(
            model_name="qwen3-plus",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            doc_url="https://example.com/docs",
            snippet="thinking.enabled=true",
        )
    )
    meta = out.get("orchestrator") or {}
    assert meta.get("mode") == "dual"
    assert meta.get("conflict") is True
    assert CompareSubagent.name in (meta.get("used_subagents") or [])
    conflict_detail = meta.get("conflict_detail") or {}
    assert conflict_detail.get("decision") == "url"
    assert isinstance(conflict_detail.get("scoring"), dict)
    assert seen["compare"] == 1


def test_submit_dual_source_falls_back_to_available_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    orch = ThinkingRecipeOrchestrator(workspace=tmp_path)

    class _BadCode:
        name = CodeExtractorSubagent.name

        def run(self, **kwargs):
            return ExtractionResult(
                source_type="snippet",
                candidates={},
                evidence=[],
                confidence=0.1,
                ok=False,
                error="snippet_extraction_failed",
            )

    class _GoodUrl:
        name = UrlExtractorSubagent.name

        def run(self, **kwargs):
            return ExtractionResult(
                source_type="url",
                candidates={
                    "enabled_param": "enable_thinking",
                    "effort_param": "thinking.effort",
                    "allowed_efforts": ["high", "max"],
                },
                evidence=[{"kind": "url", "source": "https://example.com/docs", "quote": "enable_thinking"}],
                confidence=0.9,
                ok=True,
            )

    monkeypatch.setitem(orch._subagents, CodeExtractorSubagent.name, _BadCode())
    monkeypatch.setitem(orch._subagents, UrlExtractorSubagent.name, _GoodUrl())

    out = asyncio.run(
        orch.submit(
            model_name="qwen3-plus",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            doc_url="https://example.com/docs",
            snippet="x",
        )
    )
    meta = out.get("orchestrator") or {}
    assert meta.get("mode") == "dual_fallback_url"
    assert meta.get("decision") == "url"


def test_compare_score_recency_by_source_type() -> None:
    agent = CompareSubagent()
    base_url = "https://api.not-matching-host.com/v1"
    model_name = "x-model"
    snippet_result = ExtractionResult(
        source_type="snippet",
        candidates={"enabled_param": "thinking.enabled", "effort_param": "reasoning_effort", "allowed_efforts": ["high"]},
        evidence=[{"kind": "snippet", "source": "inline", "quote": "thinking.enabled"}],
        confidence=0.8,
        ok=True,
    )
    url_ok_result = ExtractionResult(
        source_type="url",
        candidates={"enabled_param": "enable_thinking", "effort_param": "thinking.effort", "allowed_efforts": ["high"]},
        evidence=[{"kind": "url", "source": "https://docs.example.com", "quote": "enable_thinking"}],
        confidence=0.9,
        ok=True,
    )
    url_err_result = ExtractionResult(
        source_type="url",
        candidates={"enabled_param": "enable_thinking", "effort_param": "thinking.effort", "allowed_efforts": ["high"]},
        evidence=[
            {"kind": "url", "source": "https://docs.example.com", "quote": "enable_thinking"},
            {"kind": "url_error", "source": "https://docs.example.com", "quote": "timeout"},
        ],
        confidence=0.45,
        ok=True,
    )

    sn_score = agent._score(result=snippet_result, model_name=model_name, base_url=base_url)
    ok_score = agent._score(result=url_ok_result, model_name=model_name, base_url=base_url)
    err_score = agent._score(result=url_err_result, model_name=model_name, base_url=base_url)

    assert ok_score["recency"] == 0.7
    assert sn_score["recency"] == 0.5
    assert err_score["recency"] == 0.4
    assert ok_score["recency"] > sn_score["recency"] > err_score["recency"]
