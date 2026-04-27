"""Tools for global thinking recipe lookup/compile function-calling."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import StringSchema, tool_parameters_schema
from nanobot.profile import ThinkingRecipeStore


@tool_parameters(
    tool_parameters_schema(
        model_name=StringSchema("Target model name, e.g. deepseek-v4-flash"),
        base_url=StringSchema("Target model base URL, e.g. https://api.deepseek.com"),
        required=["model_name", "base_url"],
    )
)
class ThinkingRecipeLookupTool(Tool):
    """Lookup a persisted global thinking recipe by model signature."""

    name = "thinking_recipe_lookup"
    description = (
        "Lookup global thinking recipe by model_name + base_url. "
        "Returns found=false when no recipe exists."
    )

    def __init__(self, workspace: Path | None = None):
        self._store = ThinkingRecipeStore(workspace)

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, model_name: str, base_url: str, **kwargs: Any) -> str:
        clean_model = (model_name or "").strip()
        clean_base = (base_url or "").strip()
        if not clean_model:
            return "Error: model_name is required"
        if not clean_base:
            return "Error: base_url is required"
        recipe = self._store.lookup(model_name=clean_model, base_url=clean_base)
        payload: dict[str, Any] = {"found": recipe is not None}
        if recipe is not None:
            payload["recipe"] = recipe
        return json.dumps(payload, ensure_ascii=False)


@tool_parameters(
    tool_parameters_schema(
        model_name=StringSchema("Target model name, e.g. deepseek-v4-flash"),
        base_url=StringSchema("Target model base URL, e.g. https://api.deepseek.com"),
        doc_url=StringSchema(
            "Optional official documentation URL for thinking parameters",
            nullable=True,
        ),
        snippet=StringSchema(
            "Optional official sample code or parameter format",
            nullable=True,
        ),
        required=["model_name", "base_url"],
    )
)
class ThinkingRecipeCompileTool(Tool):
    """Compile evidence (url/snippet) into normalized preview recipe parameters."""

    name = "thinking_recipe_compile"
    description = (
        "Compile evidence (doc_url and/or snippet) to normalized thinking "
        "parameter recipe preview. Does not persist any data."
    )

    def __init__(self, workspace: Path | None = None):
        self._store = ThinkingRecipeStore(workspace)

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        model_name: str,
        base_url: str,
        doc_url: str | None = None,
        snippet: str | None = None,
        **kwargs: Any,
    ) -> str:
        clean_model = (model_name or "").strip()
        clean_base = (base_url or "").strip()
        if not clean_model:
            return "Error: model_name is required"
        if not clean_base:
            return "Error: base_url is required"
        try:
            compiled = self._store.compile(
                model_name=clean_model,
                base_url=clean_base,
                doc_url=doc_url,
                snippet=snippet,
            )
        except ValueError as e:
            return f"Error: {e}"
        return json.dumps(compiled, ensure_ascii=False)
