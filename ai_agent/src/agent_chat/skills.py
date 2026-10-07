"""Skills loader — reuses the repo's Claude Code ``SKILL.md`` files as tools.

Mirrors the role of ``mcp_tools.py``: discovers skills from config-declared
directories and exposes each as a LangChain tool. Calling ``skill_<name>``
returns that skill's full markdown body as an instruction block for the LLM to
follow — exactly how Claude Code expands a skill on invocation. A catalog of
available skills (name + description) is injected into the system prompt so the
LLM knows *when* to trigger each one.

The langchain import is guarded inside ``build_skill_tools`` (like
``mcp_tools.get_tools`` lazily imports its client) so importing this module
never requires langchain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

# Frontmatter delimited by a leading `---` line and a closing `---` line.
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


@dataclass
class Skill:
    """One discovered ``SKILL.md``: its frontmatter metadata + markdown body."""

    name: str
    description: str
    body: str
    path: Path


def _parse_skill(path: Path) -> Skill | None:
    """Parse a ``SKILL.md`` into a ``Skill``; return None if no valid frontmatter."""
    text = path.read_text()
    match = _FRONTMATTER.match(text)
    if not match:
        return None
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(meta, dict):
        return None
    name = str(meta.get("name") or path.parent.name)
    description = str(meta.get("description") or "")
    body = match.group(2).strip()
    return Skill(name=name, description=description, body=body, path=path)


def resolve_skill_dirs(entries: list[str], base: Path) -> list[Path]:
    """Resolve config skill paths against ``base``. ``~`` is the user home directory."""
    resolved: list[Path] = []
    for raw in entries:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = base / path
        resolved.append(path)
    return resolved


def discover_skills(paths: list[Path]) -> list[Skill]:
    """Discover skills under the given roots.

    Each path is treated as a skills root (glob ``*/SKILL.md``); a path that
    points directly at a skill directory containing ``SKILL.md`` is also
    accepted. Files without valid frontmatter are skipped. De-duped by ``name``
    (first wins).
    """
    skills: list[Skill] = []
    seen: set[str] = set()
    for root in paths:
        if not root.exists():
            continue
        candidates: list[Path] = []
        direct = root / "SKILL.md"
        if direct.is_file():
            candidates.append(direct)
        candidates.extend(sorted(root.glob("*/SKILL.md")))
        for md in candidates:
            skill = _parse_skill(md)
            if skill is None or skill.name in seen:
                continue
            seen.add(skill.name)
            skills.append(skill)
    return skills


_SLASH = re.compile(r"^/([A-Za-z0-9][A-Za-z0-9_-]*)(?:\s+|$)([\s\S]*)$")


def parse_slash(query: str) -> tuple[str, str] | None:
    """``/name rest`` at the start of a message. Returns ``(name, rest)`` or None."""
    match = _SLASH.match(query.strip())
    if not match:
        return None
    return match.group(1), match.group(2).strip()


def find_skill(skills: list[Skill], name: str) -> Skill | None:
    """Match a slash name to a loaded skill. ``skill_sap_jira`` matches ``sap-jira``."""
    wanted = _sanitize(name)
    for skill in skills:
        if skill.name == name or _sanitize(skill.name) == wanted:
            return skill
    return None


def format_skills_message(skills: list[Skill]) -> str:
    """Reply for ``/skills``: every skill this config can actually invoke."""
    if not skills:
        return "当前配置没有加载 skill。在该配置的 YAML 里加上 skills 目录后再试。"
    lines = ["当前已加载的 skill：", ""]
    for skill in skills:
        description = " ".join(skill.description.split())
        lines.append(f"- `/{skill.name}` — {description}")
    lines.append("")
    lines.append("发送 `/技能名`，后面可以写具体请求。例如 `/sap-jira`、`/kb-capture`、`/sap-authentication`。")
    return "\n".join(lines)


def missing_skill_message(name: str, skills: list[Skill]) -> str:
    loaded = ", ".join(f"`/{skill.name}`" for skill in skills) or "（无）"
    return f"`/{name}` 没有加载，不能调用。\n\n当前已加载：{loaded}\n\n发送 `/skills` 查看完整列表。"


def skill_turn_query(skill: Skill, rest: str) -> str:
    """User turn whose instructions are already the skill body.

    The body is in the message, so the model follows it even when it does not
    call the skill tool. The tool must not be called again or the body is duplicated.
    """
    request = rest or "用户没有附加说明。按该 skill 的起始步骤开始，并先确认还缺什么。"
    return (
        f"用户通过 /{skill.name} 调用了这个 skill。下面的正文就是该 skill 的指令，已经加载完成。"
        f"按正文执行，不要再调用 {skill_tool_name(skill.name)}。\n\n"
        f"# Skill: {skill.name}\n\n{skill.body}\n\n"
        f"---\n用户请求：\n{request}"
    )


def _sanitize(name: str) -> str:
    """Turn a skill name into a valid tool identifier (hyphens/spaces → ``_``)."""
    return re.sub(r"[^0-9A-Za-z_]", "_", name)


def skill_tool_name(name: str) -> str:
    """LangChain tool name for a skill, matching ``/name`` in the composer."""
    return f"skill_{_sanitize(name)}"


def build_skill_tools(skills: list[Skill]) -> list[BaseTool]:
    """Expose each skill as a no-arg ``skill_<name>`` tool returning its body."""
    if not skills:
        return []

    from langchain_core.tools import StructuredTool

    tools: list[BaseTool] = []
    for skill in skills:
        body = skill.body
        name = skill.name

        def _run(_body: str = body, _name: str = name) -> str:
            return (
                f"# Skill: {_name}\n\n{_body}\n\n---\n"
                "Follow these instructions in this turn. "
                "When this skill covers multiple sources or says to spawn subagents, "
                "call delegate_source once per source in the same turn and pass that "
                "source's instructions in task. Then synthesize the summaries. "
                "When the request is a single source, call that source's mcp__* tools "
                "or web_search directly and do not delegate. "
                "If a named MCP server is not connected, say it is not connected. "
                "点名后必须调用、不得声称未发生的调用。"
            )

        tools.append(
            StructuredTool.from_function(
                func=_run,
                name=skill_tool_name(name),
                description=skill.description,
            )
        )
    return tools


def skills_catalog(skills: list[Skill]) -> str:
    """Render the Claude-Code-style "Available skills" advertisement block.

    Returns ``""`` when there are no skills (caller leaves the prompt untouched).
    """
    if not skills:
        return ""
    lines = [
        "# Available skills",
        "You can invoke a skill by calling its tool (skill_<name>). Each returns",
        "instructions you should then follow. Use a skill when the request matches its description.",
        "用户点了 skill 名或 skill_<name> 时，先调用该 skill 工具，再执行正文里点名、且当前工具列表里存在的工具。",
        "用户消息以 /<name> 开头且正文已经包含该 skill 时，按正文执行，不要再调用 skill_<name>。",
        "/skills 只是列出已加载的 skill，不需要调用工具。",
        "点名后必须调用、不得声称未发生的调用。",
        "多源 prompt 或技能要求并行覆盖多个数据源时，同一回合对每个已接入的源各调用一次 delegate_source，再综合摘要。",
        "单源问题直接调用对应的 mcp__* 或 web_search，不要委托。",
        "工具列表里没有的服务要说明未接入，不能写成已经查过。",
        "",
    ]
    lines.extend(f"- {skill_tool_name(s.name)}: {s.description}" for s in skills)
    return "\n".join(lines)
