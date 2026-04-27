---
name: ThinkingParameterExtraction
description: Extract and reconcile official thinking-mode parameters from URL and/or code evidence. Use when a model lacks stored thinking recipe and user provides docs URL, snippet, or both.
---

# ThinkingParameterExtraction

This skill defines extraction and evidence rules only.
It does not orchestrate the full websocket workflow.

## Scope

- Goal: produce normalized evidence that can be compiled into a thinking recipe preview.
- Non-goal: trigger save automatically.
- Save boundary: human confirms in frontend, then backend `save` API persists.

## Inputs

Accept only:
- Official documentation URL (`doc_url`)
- Official code sample or parameter snippet (`snippet`)

Required runtime context:
- `model_name`
- `base_url`

## Routing Rules

- Only `snippet`: run `code_extractor_subagent` only.
- Only `doc_url`: run `url_extractor_subagent` only.
- `doc_url` + `snippet`: run both extractors, then check consistency.
- Run `compare_subagent` only when dual-source consistency check reports conflict.

## Subagent Contracts

### `code_extractor_subagent`

Responsibilities:
- Clean snippet (remove unrelated prose/noise).
- Extract candidate thinking control fields and allowed values.
- Keep source evidence and exact quoted fragments.

Output shape:
```json
{
  "source_type": "snippet",
  "evidence": [{"kind": "snippet", "source": "inline", "quote": "..."}],
  "candidates": {
    "enabled_param": "thinking.enabled",
    "effort_param": "reasoning_effort",
    "allowed_efforts": ["low", "medium", "high"]
  },
  "confidence": 0.0
}
```

### `url_extractor_subagent`

Responsibilities:
- Fetch page content.
- Clean HTML and extract main body text + API example blocks.
- Keep source URL and evidence anchors.

Output shape:
```json
{
  "source_type": "url",
  "evidence": [{"kind": "url", "source": "https://...", "quote": "..."}],
  "candidates": {
    "enabled_param": "enable_thinking",
    "effort_param": "thinking.effort",
    "allowed_efforts": ["high", "max"]
  },
  "confidence": 0.0
}
```

### `compare_subagent`

Run only when both sources exist and conflict.

Responsibilities:
- Compare candidate parameters from URL and snippet evidence.
- Score and rank by:
  - credibility
  - completeness
  - executability
  - recency
  - model match
- Produce one merged final candidate and decision reason.

Output shape:
```json
{
  "source_type": "merged",
  "decision": "url|snippet|hybrid",
  "scoring": {
    "url": {"credibility": 0.0, "completeness": 0.0, "executability": 0.0, "recency": 0.0, "model_match": 0.0},
    "snippet": {"credibility": 0.0, "completeness": 0.0, "executability": 0.0, "recency": 0.0, "model_match": 0.0}
  },
  "evidence": [{"kind": "url|snippet", "source": "...", "quote": "..."}],
  "candidates": {
    "enabled_param": "thinking.enabled",
    "effort_param": "reasoning_effort",
    "allowed_efforts": ["high", "max"]
  }
}
```

## Tool Boundaries

- `thinking_recipe_lookup`: can be called to check existing global recipe.
- `thinking_recipe_compile`: can be called to turn evidence into preview recipe.
- `thinking_recipe_save`: do not call automatically from this skill.

## Quality Gates

- Do not invent undocumented parameters.
- Mark uncertain fields explicitly.
- Every candidate field must have evidence.
- Preserve original source URL/snippet fragments for UI confirmation.
