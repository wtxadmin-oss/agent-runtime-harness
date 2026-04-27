from __future__ import annotations

import asyncio
import json

from nanobot.agent.tools.thinking_recipe import ThinkingRecipeCompileTool, ThinkingRecipeLookupTool
from nanobot.profile import ThinkingRecipeStore


def test_lookup_returns_found_false_when_missing(tmp_path) -> None:
    tool = ThinkingRecipeLookupTool(workspace=tmp_path)

    raw = asyncio.run(
        tool.execute(
            model_name="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
        )
    )
    out = json.loads(raw)

    assert out == {"found": False}


def test_lookup_returns_recipe_when_exists(tmp_path) -> None:
    store = ThinkingRecipeStore(tmp_path)
    compiled = store.compile(
        model_name="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        doc_url="https://api.deepseek.com/docs/reasoning",
        snippet=None,
    )
    store.save(recipe=compiled["recipe"], evidence=compiled["evidence"])
    tool = ThinkingRecipeLookupTool(workspace=tmp_path)

    raw = asyncio.run(
        tool.execute(
            model_name="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
        )
    )
    out = json.loads(raw)

    assert out["found"] is True
    assert out["recipe"]["model_name"] == "deepseek-v4-flash"
    assert out["recipe"]["validated"] is True


def test_compile_supports_url_or_snippet_or_both(tmp_path) -> None:
    tool = ThinkingRecipeCompileTool(workspace=tmp_path)

    raw_url = asyncio.run(
        tool.execute(
            model_name="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            doc_url="https://api.deepseek.com/docs/reasoning",
        )
    )
    out_url = json.loads(raw_url)
    assert out_url.get("preview_id")
    assert out_url["recipe"]["model_name"] == "deepseek-v4-flash"

    raw_snippet = asyncio.run(
        tool.execute(
            model_name="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            snippet="thinking: { enabled: true }",
        )
    )
    out_snippet = json.loads(raw_snippet)
    assert out_snippet.get("preview_id")

    raw_both = asyncio.run(
        tool.execute(
            model_name="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            doc_url="https://api.deepseek.com/docs/reasoning",
            snippet="thinking.enabled=true",
        )
    )
    out_both = json.loads(raw_both)
    assert out_both.get("preview_id")


def test_compile_requires_doc_url_or_snippet(tmp_path) -> None:
    tool = ThinkingRecipeCompileTool(workspace=tmp_path)

    out = asyncio.run(
        tool.execute(
            model_name="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
        )
    )

    assert out.startswith("Error:")
    assert "doc_url_or_snippet_required" in out
