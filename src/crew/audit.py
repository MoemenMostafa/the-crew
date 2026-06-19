"""Append-only audit log: one JSON line per agent tool action / guardrail decision."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Optional


class AuditLog:
    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self._clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        persona: str,
        tool: str,
        summary: str,
        channel: Optional[str] = None,
        decision: Optional[str] = None,
    ) -> None:
        entry = {
            "ts": self._clock(),
            "persona": persona,
            "tool": tool,
            "summary": summary,
        }
        if channel is not None:
            entry["channel"] = channel
        if decision is not None:
            entry["decision"] = decision
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
