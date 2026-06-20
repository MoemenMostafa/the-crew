"""Wraps the Claude Agent SDK as one resumable persona session.

All `claude_agent_sdk` access is isolated here, so the rest of the Crew is
SDK-agnostic. Each turn:
  * injects the persona's persistent memory into the system prompt,
  * routes every tool call through the guardrail permission hook (which also
    audits),
  * accumulates the assistant's text reply, and
  * remembers the session id so the next turn resumes the same conversation.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

from .audit import AuditLog
from .guardrails import make_can_use_tool
from .memory import Memory
from .persona import Persona
from .state import SessionStore


class AgentSession:
    def __init__(
        self,
        persona: Persona,
        audit: AuditLog,
        memory: Memory,
        client_factory: Callable[..., object] = ClaudeSDKClient,
        store: Optional[SessionStore] = None,
    ):
        self.persona = persona
        self.audit = audit
        self.memory = memory
        self._client_factory = client_factory
        self.store = store
        # Resume the prior conversation across restarts when a store is present.
        self.session_id: Optional[str] = store.get(persona.name) if store else None
        self._can_use_tool = make_can_use_tool(
            persona.name, persona.cfg.guardrails, audit
        )

    def _options(self, system_prompt: str, resume: Optional[str]) -> ClaudeAgentOptions:
        # Route ALL tool calls through the guardrail hook (no pre-allowlist) so
        # nothing dangerous slips past unaudited.
        return ClaudeAgentOptions(
            system_prompt=system_prompt,
            cwd=self.persona.workdir,
            model=self.persona.cfg.model,
            permission_mode="default",
            can_use_tool=self._can_use_tool,
            resume=resume,
        )

    def _remember_session(self, session_id: str) -> None:
        self.session_id = session_id
        if self.store is not None:
            self.store.set(self.persona.name, session_id)

    async def ask(
        self,
        text: str,
        context: str = "",
        on_update: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> str:
        """Run one turn. If ``on_update`` is given, each of the agent's intermediate
        messages is delivered as it streams in (live progress), and the full
        transcript is returned for logging. Without it, the joined text is returned."""
        system_prompt = self.persona.system_prompt(self.memory.read())
        prompt = f"{context}\n\n{text}".strip() if context else text

        try:
            return await self._run(system_prompt, prompt, on_update, self.session_id)
        except Exception:
            # A resumed session id may be stale/missing (e.g. CLI session pruned).
            # Drop it and retry once from a fresh conversation rather than failing.
            if self.session_id is not None:
                self.session_id = None
                return await self._run(system_prompt, prompt, on_update, None)
            raise

    async def _run(
        self,
        system_prompt: str,
        prompt: str,
        on_update,
        resume: Optional[str],
    ) -> str:
        options = self._options(system_prompt, resume)
        chunks: list[str] = []
        async with self._client_factory(options=options) as client:
            await client.query(prompt)
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    joined = "".join(
                        b.text for b in msg.content if isinstance(b, TextBlock)
                    ).strip()
                    if joined:
                        chunks.append(joined)
                        if on_update is not None:
                            await on_update(joined)
                elif isinstance(msg, ResultMessage):
                    if msg.session_id:
                        self._remember_session(msg.session_id)
        return "\n\n".join(chunks).strip()
