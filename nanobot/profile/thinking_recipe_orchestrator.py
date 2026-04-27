"""Thinking recipe orchestration kernel for Phase 6.

This module provides a deterministic runtime orchestrator that routes evidence
inputs through three registered subagents:
- code_extractor_subagent
- url_extractor_subagent
- compare_subagent
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from nanobot.agent.tools.thinking_recipe import (
    ThinkingRecipeCompileTool,
    ThinkingRecipeLookupTool,
)


_WS_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_CODE_RE = re.compile(r"<code\b[^>]*>(.*?)</code>", re.IGNORECASE | re.DOTALL)


def _clean_text(text: str) -> str:
    stripped = _SCRIPT_STYLE_RE.sub(" ", text or "")
    no_tags = _TAG_RE.sub(" ", stripped)
    return _WS_RE.sub(" ", unescape(no_tags)).strip()


def _clean_snippet(text: str) -> str:
    raw = (text or "").strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = raw[3:-3].strip()
    return raw


def _infer_enabled_param(text: str) -> str | None:
    low = text.lower()
    if "thinking_enabled" in low:
        return "thinking_enabled"
    if "enable_thinking" in low:
        return "enable_thinking"
    if "thinking.enabled" in low or "thinking" in low:
        return "thinking.enabled"
    return None


def _infer_effort_param(text: str) -> str | None:
    low = text.lower()
    if "reasoning_effort" in low:
        return "reasoning_effort"
    if "thinking.effort" in low:
        return "thinking.effort"
    if "effort" in low:
        return "thinking.effort"
    return None


def _infer_allowed_efforts(text: str) -> list[str]:
    values: list[str] = []
    low = text.lower()
    for item in ("minimal", "minimum", "low", "medium", "high", "max"):
        if item in low and item not in values:
            values.append(item)
    if not values:
        values = ["high", "max"]
    return values


def _build_candidate_dict(text: str) -> dict[str, Any]:
    return {
        "enabled_param": _infer_enabled_param(text),
        "effort_param": _infer_effort_param(text),
        "allowed_efforts": _infer_allowed_efforts(text),
    }


def _candidate_signature(cand: dict[str, Any]) -> tuple[Any, Any, tuple[str, ...]]:
    efforts = cand.get("allowed_efforts")
    if not isinstance(efforts, list):
        efforts = []
    return (
        cand.get("enabled_param"),
        cand.get("effort_param"),
        tuple(str(x) for x in efforts),
    )


@dataclass(slots=True)
class ExtractionResult:
    source_type: str
    candidates: dict[str, Any]
    evidence: list[dict[str, Any]]
    confidence: float
    ok: bool = True
    error: str | None = None


class CodeExtractorSubagent:
    name = "code_extractor_subagent"

    def run(self, *, snippet: str, model_name: str, base_url: str) -> ExtractionResult:
        cleaned = _clean_snippet(snippet)
        candidates = _build_candidate_dict(cleaned)
        enabled = candidates.get("enabled_param")
        evidence = [{
            "kind": "snippet",
            "source": "inline",
            "quote": cleaned[:400],
        }]
        ok = bool(enabled)
        return ExtractionResult(
            source_type="snippet",
            candidates=candidates,
            evidence=evidence,
            confidence=0.85 if ok else 0.2,
            ok=ok,
            error=None if ok else "snippet_extraction_failed",
        )


class UrlExtractorSubagent:
    name = "url_extractor_subagent"

    def _fetch(self, url: str) -> str:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "nanobot-thinking-recipe/1.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310
            return resp.read().decode("utf-8", errors="replace")

    def run(self, *, doc_url: str, model_name: str, base_url: str) -> ExtractionResult:
        raw = ""
        fetch_error: str | None = None
        try:
            raw = self._fetch(doc_url)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            fetch_error = str(e)
        cleaned = _clean_text(raw)
        code_blocks = _CODE_RE.findall(raw)
        joined_code = "\n".join(_clean_text(x) for x in code_blocks[:4])
        combined = f"{cleaned}\n{joined_code}".strip()
        candidates = _build_candidate_dict(combined)
        enabled = candidates.get("enabled_param")
        evidence = [{
            "kind": "url",
            "source": doc_url,
            "quote": combined[:500] if combined else "",
        }]
        if fetch_error:
            evidence.append({
                "kind": "url_error",
                "source": doc_url,
                "quote": fetch_error[:300],
            })
        ok = bool(enabled)
        confidence = 0.9 if ok and not fetch_error else (0.45 if ok else 0.15)
        return ExtractionResult(
            source_type="url",
            candidates=candidates,
            evidence=evidence,
            confidence=confidence,
            ok=ok,
            error=None if ok else "url_extraction_failed",
        )


class CompareSubagent:
    name = "compare_subagent"

    @staticmethod
    def _score(
        *,
        result: ExtractionResult,
        model_name: str,
        base_url: str,
    ) -> dict[str, float]:
        cand = result.candidates
        has_enabled = 1.0 if cand.get("enabled_param") else 0.0
        has_effort = 1.0 if cand.get("effort_param") else 0.0
        allowed = cand.get("allowed_efforts") or []
        complete = min(1.0, (has_enabled + has_effort + (0.5 if allowed else 0.0)) / 2.5)
        credibility = max(0.2, min(1.0, result.confidence))
        exec_score = 1.0 if has_enabled else 0.2
        has_url_error = any(str(ev.get("kind") or "") == "url_error" for ev in result.evidence)
        if result.source_type == "snippet":
            recency = 0.5
        elif has_url_error:
            recency = 0.4
        else:
            recency = 0.7
        model_match = 0.5
        model_low = model_name.lower()
        for ev in result.evidence:
            quote = str(ev.get("quote") or "").lower()
            src = str(ev.get("source") or "").lower()
            if model_low and model_low in quote:
                model_match = 1.0
            if base_url:
                try:
                    base_host = urlparse(base_url).netloc.lower()
                    src_host = urlparse(src).netloc.lower()
                    if base_host and src_host and base_host in src_host:
                        credibility = min(1.0, credibility + 0.1)
                        recency = 0.8
                except Exception:
                    pass
        return {
            "credibility": credibility,
            "completeness": complete,
            "executability": exec_score,
            "recency": recency,
            "model_match": model_match,
        }

    def run(
        self,
        *,
        url_result: ExtractionResult,
        snippet_result: ExtractionResult,
        model_name: str,
        base_url: str,
    ) -> dict[str, Any]:
        url_sc = self._score(result=url_result, model_name=model_name, base_url=base_url)
        sn_sc = self._score(result=snippet_result, model_name=model_name, base_url=base_url)
        url_total = sum(url_sc.values())
        sn_total = sum(sn_sc.values())
        if url_total > sn_total + 0.15:
            decision = "url"
            chosen = url_result.candidates
        elif sn_total > url_total + 0.15:
            decision = "snippet"
            chosen = snippet_result.candidates
        else:
            decision = "hybrid"
            chosen = {
                "enabled_param": url_result.candidates.get("enabled_param")
                or snippet_result.candidates.get("enabled_param"),
                "effort_param": url_result.candidates.get("effort_param")
                or snippet_result.candidates.get("effort_param"),
                "allowed_efforts": list(dict.fromkeys(
                    list(url_result.candidates.get("allowed_efforts") or [])
                    + list(snippet_result.candidates.get("allowed_efforts") or [])
                )) or ["high", "max"],
            }
        evidence = list(url_result.evidence) + list(snippet_result.evidence)
        return {
            "source_type": "merged",
            "decision": decision,
            "scoring": {
                "url": url_sc,
                "snippet": sn_sc,
            },
            "evidence": evidence,
            "candidates": chosen,
        }


class ThinkingRecipeOrchestrator:
    """Phase 6 orchestration kernel for thinking recipe extraction."""

    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = workspace
        self.lookup_tool = ThinkingRecipeLookupTool(workspace=workspace)
        self.compile_tool = ThinkingRecipeCompileTool(workspace=workspace)
        self._subagents: dict[str, Any] = {}
        self._register_runtime_subagents()

    def _register_runtime_subagents(self) -> None:
        self._subagents = {
            CodeExtractorSubagent.name: CodeExtractorSubagent(),
            UrlExtractorSubagent.name: UrlExtractorSubagent(),
            CompareSubagent.name: CompareSubagent(),
        }

    @property
    def registered_subagents(self) -> list[str]:
        return sorted(self._subagents.keys())

    async def _lookup(self, *, model_name: str, base_url: str) -> dict[str, Any]:
        raw = await self.lookup_tool.execute(model_name=model_name, base_url=base_url)
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {"found": False}
        except json.JSONDecodeError:
            return {"found": False}

    async def _compile(
        self,
        *,
        model_name: str,
        base_url: str,
        doc_url: str | None,
        snippet: str | None,
    ) -> dict[str, Any]:
        raw = await self.compile_tool.execute(
            model_name=model_name,
            base_url=base_url,
            doc_url=doc_url,
            snippet=snippet,
        )
        if raw.startswith("Error: "):
            raise ValueError(raw.replace("Error: ", "", 1))
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("compile_failed")
        return payload

    @staticmethod
    def _conflict(a: ExtractionResult, b: ExtractionResult) -> bool:
        return _candidate_signature(a.candidates) != _candidate_signature(b.candidates)

    @staticmethod
    def _candidates_to_snippet(cand: dict[str, Any]) -> str:
        enabled = cand.get("enabled_param") or "thinking.enabled"
        effort = cand.get("effort_param")
        allowed = cand.get("allowed_efforts") or ["high", "max"]
        lines = [f"{enabled} = true"]
        if effort:
            lines.append(f"{effort} = \"{allowed[0]}\"")
        return "\n".join(lines)

    async def submit(
        self,
        *,
        model_name: str,
        base_url: str,
        doc_url: str | None,
        snippet: str | None,
        on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        async def _emit(stage: str, message: str, meta: dict[str, Any] | None = None) -> None:
            if on_progress is None:
                return
            payload: dict[str, Any] = {
                "stage": stage,
                "message": message,
            }
            if meta:
                payload["meta"] = meta
            await on_progress(payload)

        clean_model = (model_name or "").strip()
        clean_base = (base_url or "").strip()
        clean_url = (doc_url or "").strip()
        clean_snippet = (snippet or "").strip()
        if not clean_model:
            raise ValueError("model_name is required")
        if not clean_base:
            raise ValueError("base_url is required")
        if not clean_url and not clean_snippet:
            raise ValueError("doc_url_or_snippet_required")
        await _emit("submitted", "request accepted")

        # submitted -> lookup_checked
        _ = await self._lookup(model_name=clean_model, base_url=clean_base)

        code_agent: CodeExtractorSubagent = self._subagents[CodeExtractorSubagent.name]
        url_agent: UrlExtractorSubagent = self._subagents[UrlExtractorSubagent.name]
        compare_agent: CompareSubagent = self._subagents[CompareSubagent.name]

        extract_note = {
            "mode": "single",
            "used_subagents": [],
            "conflict": False,
            "decision": "single",
        }

        if clean_snippet and not clean_url:
            # source_routed -> extracted
            await _emit("extracting", "extracting snippet evidence", {"mode": "snippet_only"})
            code_res = code_agent.run(
                snippet=clean_snippet,
                model_name=clean_model,
                base_url=clean_base,
            )
            extract_note["used_subagents"] = [CodeExtractorSubagent.name]
            if not code_res.ok:
                raise ValueError("snippet_extraction_failed")
            compile_snippet = self._candidates_to_snippet(code_res.candidates)
            await _emit("compiling", "compiling recipe", {"mode": "snippet_only"})
            compiled = await self._compile(
                model_name=clean_model,
                base_url=clean_base,
                doc_url=None,
                snippet=compile_snippet,
            )
            compiled["orchestrator"] = extract_note
            await _emit("preview_ready", "recipe preview ready", {"mode": "snippet_only"})
            return compiled

        if clean_url and not clean_snippet:
            # source_routed -> extracted
            await _emit("extracting", "extracting url evidence", {"mode": "url_only"})
            url_res = url_agent.run(
                doc_url=clean_url,
                model_name=clean_model,
                base_url=clean_base,
            )
            extract_note["used_subagents"] = [UrlExtractorSubagent.name]
            if not url_res.ok:
                raise ValueError("url_extraction_failed")
            compile_snippet = self._candidates_to_snippet(url_res.candidates)
            await _emit("compiling", "compiling recipe", {"mode": "url_only"})
            compiled = await self._compile(
                model_name=clean_model,
                base_url=clean_base,
                doc_url=clean_url,
                snippet=compile_snippet,
            )
            compiled["orchestrator"] = extract_note
            await _emit("preview_ready", "recipe preview ready", {"mode": "url_only"})
            return compiled

        # URL + snippet: dual extraction
        extract_note["mode"] = "dual"
        await _emit("extracting", "extracting url and snippet evidence", {"mode": "dual"})
        code_res = code_agent.run(
            snippet=clean_snippet,
            model_name=clean_model,
            base_url=clean_base,
        )
        url_res = url_agent.run(
            doc_url=clean_url,
            model_name=clean_model,
            base_url=clean_base,
        )
        extract_note["used_subagents"] = [
            CodeExtractorSubagent.name,
            UrlExtractorSubagent.name,
        ]
        if not code_res.ok and not url_res.ok:
            raise ValueError("dual_source_extraction_failed")

        if code_res.ok and not url_res.ok:
            extract_note["mode"] = "dual_fallback_snippet"
            extract_note["decision"] = "snippet"
            compile_snippet = self._candidates_to_snippet(code_res.candidates)
            await _emit("compiling", "compiling recipe", {"mode": "dual_fallback_snippet"})
            compiled = await self._compile(
                model_name=clean_model,
                base_url=clean_base,
                doc_url=clean_url,
                snippet=compile_snippet,
            )
            compiled["orchestrator"] = extract_note
            await _emit("preview_ready", "recipe preview ready", {"mode": "dual_fallback_snippet"})
            return compiled

        if url_res.ok and not code_res.ok:
            extract_note["mode"] = "dual_fallback_url"
            extract_note["decision"] = "url"
            compile_snippet = self._candidates_to_snippet(url_res.candidates)
            await _emit("compiling", "compiling recipe", {"mode": "dual_fallback_url"})
            compiled = await self._compile(
                model_name=clean_model,
                base_url=clean_base,
                doc_url=clean_url,
                snippet=compile_snippet,
            )
            compiled["orchestrator"] = extract_note
            await _emit("preview_ready", "recipe preview ready", {"mode": "dual_fallback_url"})
            return compiled

        merged_candidates = code_res.candidates
        if self._conflict(url_res, code_res):
            extract_note["conflict"] = True
            cmp = compare_agent.run(
                url_result=url_res,
                snippet_result=code_res,
                model_name=clean_model,
                base_url=clean_base,
            )
            extract_note["used_subagents"].append(CompareSubagent.name)
            extract_note["decision"] = str(cmp.get("decision") or "hybrid")
            extract_note["conflict_detail"] = {
                "decision": extract_note["decision"],
                "scoring": cmp.get("scoring") or {},
                "evidence": cmp.get("evidence") or [],
            }
            merged_candidates = cmp.get("candidates") or merged_candidates

        compile_snippet = self._candidates_to_snippet(merged_candidates)
        await _emit("compiling", "compiling recipe", {"mode": "dual"})
        compiled = await self._compile(
            model_name=clean_model,
            base_url=clean_base,
            doc_url=clean_url,
            snippet=compile_snippet,
        )
        compiled["orchestrator"] = extract_note
        await _emit("preview_ready", "recipe preview ready", {"mode": "dual"})
        return compiled
