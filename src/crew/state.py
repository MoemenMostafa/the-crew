"""Persists each persona's Claude Agent SDK session ids so conversations survive
restarts. Without this, every `./run.sh` would start the agents from scratch.

Session ids are keyed by ``(persona, conversation)`` — a *conversation* is one
Slack thread (or a DM/channel for un-threaded messages). Keeping one SDK session
per thread (rather than one per persona) means a persona resumes the *right*
thread's history instead of dragging every channel's context into every turn —
the resumed session already carries that thread's prior turns, so the full Slack
transcript only needs to be re-sent when a thread is brand new (see Router)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class SessionStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def get(self, persona: str, conversation: str) -> Optional[str]:
        convs = self._load().get(persona)
        # Legacy format stored a single id string per persona — treat as absent so
        # the conversation simply starts fresh once after upgrading.
        if not isinstance(convs, dict):
            return None
        return convs.get(conversation)

    def set(self, persona: str, conversation: str, session_id: str) -> None:
        data = self._load()
        convs = data.get(persona)
        if not isinstance(convs, dict):
            convs = {}
            data[persona] = convs
        convs[conversation] = session_id
        self.path.write_text(json.dumps(data, indent=2))
