"""Append-only audit log: one JSON line per agent tool action / guardrail decision."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Optional


class AuditLog:
    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self._clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, entry: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

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
        self._write(entry)

    def record_usage(
        self,
        persona: str,
        model: str,
        usage: Any = None,
        cost_usd: Optional[float] = None,
        num_turns: Optional[int] = None,
        channel: Optional[str] = None,
    ) -> None:
        """Record one turn's token spend so cost can be attributed per persona /
        model / channel. Token fields are flattened out of the SDK's ``usage`` dict
        (input/output + cache reads/writes) when present."""
        entry = {
            "ts": self._clock(),
            "persona": persona,
            "event": "usage",
            "model": model,
        }
        if isinstance(usage, dict):
            for k in (
                "input_tokens",
                "output_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            ):
                if usage.get(k) is not None:
                    entry[k] = usage[k]
        if cost_usd is not None:
            entry["cost_usd"] = cost_usd
        if num_turns is not None:
            entry["num_turns"] = num_turns
        if channel is not None:
            entry["channel"] = channel
        self._write(entry)
