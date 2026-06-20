"""Persists each persona's Claude Agent SDK session id so conversations survive
restarts. Without this, every `./run.sh` would start the agents from scratch."""

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

    def get(self, persona: str) -> Optional[str]:
        return self._load().get(persona)

    def set(self, persona: str, session_id: str) -> None:
        data = self._load()
        data[persona] = session_id
        self.path.write_text(json.dumps(data, indent=2))
