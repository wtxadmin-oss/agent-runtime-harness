"""Tests for global thinking recipe storage."""

from __future__ import annotations

import json
from pathlib import Path

from nanobot.profile import thinking_recipe_store as trs


def _create_store(monkeypatch, tmp_path: Path) -> trs.ThinkingRecipeStore:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(trs, "_workspace_root", lambda: workspace)
    return trs.ThinkingRecipeStore()


def test_compile_and_save_persists_global_recipe_and_evidence(monkeypatch, tmp_path: Path) -> None:
    store = _create_store(monkeypatch, tmp_path)
    compiled = store.compile(
        model_name="deepseek-v4-flash",
        base_url="https://api.deepseek.com/",
        doc_url="https://api.deepseek.com/docs/reasoning",
        snippet="thinking: { enabled: true, effort: 'high' }",
    )
    saved = store.save(
        recipe=compiled["recipe"],
        evidence=compiled["evidence"],
    )

    assert isinstance(compiled["preview_id"], str) and compiled["preview_id"]
    assert saved["model_signature"] == "https://api.deepseek.com::deepseek-v4-flash"
    assert saved["created"] is True
    lookup = store.lookup(model_name="deepseek-v4-flash", base_url="https://api.deepseek.com")
    assert lookup is not None
    assert lookup["controls"]["enabled_param"] == "thinking.enabled"
    assert lookup["validated"] is True

    recipes_path = tmp_path / "workspace" / "system" / "thinking_recipes.json"
    payload = json.loads(recipes_path.read_text(encoding="utf-8"))
    assert len(payload["recipes"]) == 1

    evidence_dir = tmp_path / "workspace" / "system" / "thinking_evidence"
    saved_files = list(evidence_dir.glob("*.json"))
    assert len(saved_files) == 2


def test_save_replaces_existing_recipe_for_same_model_signature(monkeypatch, tmp_path: Path) -> None:
    store = _create_store(monkeypatch, tmp_path)
    first = store.compile(
        model_name="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        doc_url=None,
        snippet="thinking.enabled = true",
    )
    store.save(recipe=first["recipe"], evidence=first["evidence"])
    second = store.compile(
        model_name="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        doc_url=None,
        snippet="reasoning_effort='max'",
    )
    saved = store.save(recipe=second["recipe"], evidence=second["evidence"])

    assert saved["created"] is False
    rows = store.list_recipes()
    assert len(rows) == 1
    assert rows[0]["controls"]["effort_param"] == "reasoning_effort"


def test_recipe_store_recovers_from_invalid_json(monkeypatch, tmp_path: Path) -> None:
    store = _create_store(monkeypatch, tmp_path)
    recipes_path = tmp_path / "workspace" / "system" / "thinking_recipes.json"
    recipes_path.write_text("{ broken", encoding="utf-8")

    rows = store.list_recipes()

    assert rows == []
    payload = json.loads(recipes_path.read_text(encoding="utf-8"))
    assert payload == {"recipes": []}


def test_save_replace_preserves_created_at_and_updates_updated_at(monkeypatch, tmp_path: Path) -> None:
    store = _create_store(monkeypatch, tmp_path)
    first = store.compile(
        model_name="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        doc_url=None,
        snippet="thinking.enabled = true",
    )
    store.save(recipe=first["recipe"], evidence=first["evidence"])
    before = store.lookup(model_name="deepseek-v4-flash", base_url="https://api.deepseek.com")
    assert before is not None
    before_created = before["created_at"]
    before_updated = before["updated_at"]

    second = store.compile(
        model_name="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        doc_url=None,
        snippet="reasoning_effort='max'",
    )
    store.save(recipe=second["recipe"], evidence=second["evidence"])
    after = store.lookup(model_name="deepseek-v4-flash", base_url="https://api.deepseek.com")
    assert after is not None

    assert after["created_at"] == before_created
    assert after["updated_at"] != before_updated


def test_compile_sets_validated_false_when_no_thinking_keyword(monkeypatch, tmp_path: Path) -> None:
    store = _create_store(monkeypatch, tmp_path)
    compiled = store.compile(
        model_name="some-model",
        base_url="https://api.example.com",
        doc_url=None,
        snippet="temperature=0.7, top_p=0.9",
    )

    recipe = compiled["recipe"]
    assert recipe["validated"] is False
    assert recipe["controls"]["enabled_param"] is None
