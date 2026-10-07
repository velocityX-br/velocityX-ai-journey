"""Shared test fixtures: an isolated configs dir + a fake echo LLM."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest


@pytest.fixture
def configs_dir(tmp_path: Path) -> Path:
    """A temp configs/ dir with base.yaml + a couple of use-case configs."""
    (tmp_path / "base.yaml").write_text(
        textwrap.dedent(
            """
            name: base-agent
            system_prompt: "base prompt"
            llm:
              provider: anthropic
              model: claude-sonnet-4-6
              temperature: 0.0
            memory:
              backend: memory
            recursion_limit: 25
            """
        )
    )
    (tmp_path / "generic.yaml").write_text(
        textwrap.dedent(
            """
            name: generic
            system_prompt: "you are generic"
            mcp_servers: {}
            """
        )
    )
    (tmp_path / "with_stdio.yaml").write_text(
        textwrap.dedent(
            """
            name: stdio-case
            mcp_servers:
              demo:
                transport: stdio
                command: python
                args: ["-m", "demo.server"]
                cwd: ../some/server
            """
        )
    )
    (tmp_path / "sqlite.yaml").write_text(
        textwrap.dedent(
            """
            name: sqlite-case
            memory:
              backend: sqlite
            """
        )
    )
    return tmp_path
