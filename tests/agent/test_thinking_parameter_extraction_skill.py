from __future__ import annotations

from nanobot.agent.skills import BUILTIN_SKILLS_DIR, SkillsLoader


def test_builtin_thinking_parameter_extraction_skill_is_discoverable(tmp_path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)

    loader = SkillsLoader(workspace=workspace, builtin_skills_dir=BUILTIN_SKILLS_DIR)
    entries = loader.list_skills(filter_unavailable=False)

    target = next((entry for entry in entries if entry["name"] == "ThinkingParameterExtraction"), None)
    assert target is not None
    assert target["source"] == "builtin"
    assert target["path"].endswith("ThinkingParameterExtraction/SKILL.md")
