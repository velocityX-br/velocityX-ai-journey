"""Checkpointer factory — persistence for durable, resumable conversations.

Dev uses an in-memory saver; prod uses SQLite keyed by ``thread_id`` so a
conversation can be resumed and human-in-the-loop interrupts survive restarts.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from agent_chat.config import MemoryConfig

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver


@contextmanager
def build_checkpointer(config: MemoryConfig) -> Iterator[BaseCheckpointSaver]:
    """Yield a checkpointer appropriate for the configured backend.

    Used as a context manager because the SQLite saver owns a DB connection
    that must be closed cleanly:

        with build_checkpointer(cfg) as saver:
            graph = build_graph(config, checkpointer=saver)
            ...
    """
    if config.backend == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        yield InMemorySaver()
        return

    if config.backend == "sqlite":
        from langgraph.checkpoint.sqlite import SqliteSaver

        assert config.path  # guaranteed by MemoryConfig validator
        with SqliteSaver.from_conn_string(config.path) as saver:
            yield saver
        return

    raise ValueError(f"Unsupported memory backend: {config.backend}")
