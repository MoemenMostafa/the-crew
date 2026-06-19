"""Persistent per-persona memory: a human-readable MEMORY.md index plus topic files.

Read at session start (injected into the system prompt) and updated by the agent
as it learns. Survives restarts and is independent of the LLM context window.
"""

from __future__ import annotations

import re
from pathlib import Path


def _slug(topic: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-") or "note"


class Memory:
    def __init__(self, mem_dir: str | Path):
        self.dir = Path(mem_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index = self.dir / "MEMORY.md"

    def read(self) -> str:
        parts: list[str] = []
        if self.index.exists():
            parts.append(self.index.read_text().strip())
        for f in sorted(self.dir.glob("*.md")):
            if f.name == "MEMORY.md":
                continue
            parts.append(f"### {f.stem}\n{f.read_text().strip()}")
        return "\n\n".join(p for p in parts if p)

    def append_note(self, topic: str, text: str) -> None:
        slug = _slug(topic)
        topic_file = self.dir / f"{slug}.md"
        with topic_file.open("a", encoding="utf-8") as f:
            f.write(text.rstrip() + "\n")

        # Index the topic exactly once.
        existing = self.index.read_text() if self.index.exists() else ""
        if f"({slug}.md)" not in existing:
            line = f"- [{topic}]({slug}.md)\n"
            with self.index.open("a", encoding="utf-8") as f:
                if existing and not existing.endswith("\n"):
                    f.write("\n")
                f.write(line)
