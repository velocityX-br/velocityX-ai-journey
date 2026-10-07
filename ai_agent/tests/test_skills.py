"""Skills loader tests — discovery, tool exposure, and prompt catalog.

CI-safe: no LLM and no MCP. Skips the tool-building assertions cleanly if
langchain-core is unavailable (matching test_mcp_tools's importorskip style).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_chat.skills import (
    build_skill_tools,
    discover_skills,
    find_skill,
    format_skills_message,
    parse_slash,
    resolve_skill_dirs,
    skill_turn_query,
    skills_catalog,
)


def _write_skill(root: Path, dirname: str, *, name: str, description: str, body: str) -> Path:
    skill_dir = root / dirname
    skill_dir.mkdir(parents=True)
    md = skill_dir / "SKILL.md"
    md.write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}\n")
    return md


def test_discover_parses_frontmatter(tmp_path: Path) -> None:
    _write_skill(tmp_path, "foo", name="foo", description="Does foo", body="Step 1: foo.")
    skills = discover_skills([tmp_path])
    assert len(skills) == 1
    skill = skills[0]
    assert skill.name == "foo"
    assert skill.description == "Does foo"
    assert skill.body == "Step 1: foo."


def test_discover_skips_invalid_and_dedupes(tmp_path: Path) -> None:
    # Valid skill.
    _write_skill(tmp_path, "foo", name="foo", description="Does foo", body="body")
    # No frontmatter → skipped.
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("no frontmatter here")
    # Duplicate name → first wins.
    _write_skill(tmp_path, "foo2", name="foo", description="Other foo", body="other")

    skills = discover_skills([tmp_path])
    assert [s.name for s in skills] == ["foo"]
    assert skills[0].description == "Does foo"


def test_discover_accepts_direct_skill_dir(tmp_path: Path) -> None:
    md = _write_skill(tmp_path, "foo", name="foo", description="d", body="b")
    skills = discover_skills([md.parent])
    assert [s.name for s in skills] == ["foo"]


def test_discover_missing_path_is_empty(tmp_path: Path) -> None:
    assert discover_skills([tmp_path / "nope"]) == []


def test_name_fallback_to_dir(tmp_path: Path) -> None:
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\ndescription: d\n---\nbody\n")
    skills = discover_skills([tmp_path])
    assert skills[0].name == "my-skill"


async def test_build_skill_tools_returns_body(tmp_path: Path) -> None:
    pytest.importorskip("langchain_core")
    _write_skill(tmp_path, "foo", name="foo", description="Does foo", body="RUN THE FOO")
    skills = discover_skills([tmp_path])
    tools = build_skill_tools(skills)
    assert len(tools) == 1
    assert tools[0].name == "skill_foo"
    result = await tools[0].ainvoke({})
    assert "RUN THE FOO" in str(result)
    assert "# Skill: foo" in str(result)


async def test_build_skill_tools_sanitizes_name(tmp_path: Path) -> None:
    pytest.importorskip("langchain_core")
    _write_skill(tmp_path, "multi", name="multi-source-research", description="d", body="b")
    tools = build_skill_tools(discover_skills([tmp_path]))
    assert tools[0].name == "skill_multi_source_research"


def test_build_skill_tools_empty() -> None:
    assert build_skill_tools([]) == []


def test_catalog_lists_skills(tmp_path: Path) -> None:
    _write_skill(tmp_path, "foo", name="foo", description="Does foo", body="b1")
    _write_skill(tmp_path, "bar", name="bar", description="Does bar", body="b2")
    catalog = skills_catalog(discover_skills([tmp_path]))
    assert "# Available skills" in catalog
    assert "skill_foo: Does foo" in catalog
    assert "skill_bar: Does bar" in catalog


def test_catalog_empty_is_blank() -> None:
    assert skills_catalog([]) == ""


def test_resolve_skill_dirs_expands_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    resolved = resolve_skill_dirs(["~/claude/skills", "nested"], tmp_path)
    assert resolved[0] == tmp_path / "claude" / "skills"
    assert resolved[1] == tmp_path / "nested"


def test_parse_slash_commands() -> None:
    assert parse_slash("/skills") == ("skills", "")
    assert parse_slash("/sap-jira create a bug") == ("sap-jira", "create a bug")
    assert parse_slash("/kb-capture") == ("kb-capture", "")
    assert parse_slash("please /sap-jira") is None
    assert parse_slash("/sap-authentication refresh") == ("sap-authentication", "refresh")


def test_skill_turn_includes_body(tmp_path: Path) -> None:
    _write_skill(tmp_path, "sap-jira", name="sap-jira", description="Jira", body="DO THE JIRA THING")
    skill = discover_skills([tmp_path])[0]
    query = skill_turn_query(skill, "create a bug")
    assert "DO THE JIRA THING" in query
    assert "create a bug" in query
    assert find_skill(discover_skills([tmp_path]), "sap_jira") == skill
    listing = format_skills_message(discover_skills([tmp_path]))
    assert "/sap-jira" in listing


def test_catalog_requires_a_real_call(tmp_path: Path) -> None:
    _write_skill(tmp_path, "foo", name="foo", description="Does foo", body="b1")
    catalog = skills_catalog(discover_skills([tmp_path]))
    assert "点名后必须调用、不得声称未发生的调用" in catalog
    assert "delegate_source" in catalog
    assert "没有 Agent 子代理" not in catalog
