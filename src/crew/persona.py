"""A persona: character + expertise + guardrails, all from editable files.

``personality.md`` and ``expertise.md`` live in the persona's directory and are
composed into the system prompt at session start. Editing either and calling
``reload()`` re-characterizes the agent with no code change and no service
restart.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Guardrails, PersonaConfig


def _read(path) -> str:
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return ""


def _guardrail_summary(g: Guardrails, workdir: str) -> str:
    lines = [
        "## Operating rules (enforced by the harness, not optional)",
        f"- Your working directory is `{workdir}`. Stay within it.",
        f"- Access level: **{g.access_level}**. Autonomy: **{g.autonomy}**.",
    ]
    if g.require_branch:
        lines.append(
            "- NEVER commit directly to a protected branch "
            f"({', '.join(g.protected_branches)}). Always create a feature branch "
            "and open a PR for review."
        )
    if g.block_destructive:
        lines.append(
            "- Destructive commands (force-push to protected branches, `rm -rf`, "
            "production deploys) are blocked and will be denied — don't attempt them."
        )
    if g.external_comms == "gated":
        lines.append(
            "- Any message intended for a real external customer must be posted as a "
            "DRAFT for human approval — never sent directly."
        )
    return "\n".join(lines)


@dataclass
class Persona:
    cfg: PersonaConfig
    _personality: str
    _expertise: str

    @classmethod
    def load(cls, cfg: PersonaConfig) -> "Persona":
        return cls(
            cfg=cfg,
            _personality=_read(cfg.dir / "personality.md"),
            _expertise=_read(cfg.dir / "expertise.md"),
        )

    def reload(self) -> None:
        self._personality = _read(self.cfg.dir / "personality.md")
        self._expertise = _read(self.cfg.dir / "expertise.md")

    @property
    def name(self) -> str:
        return self.cfg.name

    @property
    def workdir(self) -> str:
        return self.cfg.workdir

    def system_prompt(self, memory: str = "") -> str:
        c = self.cfg
        parts = [
            f"You are {c.display_name}, the {c.role} on an AI engineering crew "
            "that operates the Loquina product. You collaborate with the rest of the "
            "team and with the human operator over Slack.",
            "",
            "## Who you are",
            self._personality or "(personality not yet defined)",
            "",
            "## Your expertise and responsibilities",
            self._expertise or "(expertise not yet defined)",
            "",
            _guardrail_summary(c.guardrails, c.workdir),
        ]
        if memory:
            parts += [
                "",
                "## Your memory (things you've learned and decided across sessions)",
                memory,
                "",
                "Keep your memory current: when you learn something durable, record it.",
            ]
        return "\n".join(parts)
