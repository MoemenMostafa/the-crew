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
from .model_router import choose_model
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
        # One SDK session id per conversation (Slack thread / DM). Cached in memory
        # and persisted via the store so threads resume across restarts.
        self._sessions: dict[str, str] = {}
        self._can_use_tool = make_can_use_tool(
            persona.name, persona.cfg.guardrails, audit
        )

    def _resume_id(self, conversation: str) -> Optional[str]:
        if conversation in self._sessions:
            return self._sessions[conversation]
        if self.store is not None:
            return self.store.get(self.persona.name, conversation)
        return None

    def has_session(self, conversation: str) -> bool:
        """True if this persona already has a resumable session for the conversation
        — i.e. its history is already loaded and the Slack transcript needn't be
        re-sent."""
        return self._resume_id(conversation) is not None

    def _options(
        self, system_prompt: str, resume: Optional[str], model: str
    ) -> ClaudeAgentOptions:
        # Route ALL tool calls through the guardrail hook (no pre-allowlist) so
        # nothing dangerous slips past unaudited.
        opts = dict(
            system_prompt=system_prompt,
            cwd=self.persona.workdir,
            model=model,
            permission_mode="default",
            can_use_tool=self._can_use_tool,
            resume=resume,
            mcp_servers=self.persona.cfg.mcp_servers,
        )
        # Only set max_turns when configured — leaving it unset means "no cap".
        if self.persona.cfg.max_turns is not None:
            opts["max_turns"] = self.persona.cfg.max_turns
        return ClaudeAgentOptions(**opts)

    def _remember_session(self, conversation: str, session_id: str) -> None:
        self._sessions[conversation] = session_id
        if self.store is not None:
            self.store.set(self.persona.name, conversation, session_id)

    async def ask(
        self,
        text: str,
        context: str = "",
        on_update: Optional[Callable[[str], Awaitable[None]]] = None,
        channel: Optional[str] = None,
        conversation: Optional[str] = None,
        dispatch: bool = False,
        broadcast: bool = False,
    ) -> str:
        """Run one turn. If ``on_update`` is given, each of the agent's intermediate
        messages is delivered as it streams in (live progress), and the full
        transcript is returned for logging. Without it, the joined text is returned.

        ``conversation`` scopes the resumable SDK session (one per Slack thread/DM);
        it defaults to the channel, then a shared bucket. The model is chosen
        per-turn: low-stakes turns (triage, broadcasts, quick questions) use the
        persona's cheaper ``model_light`` when configured."""
        conversation = conversation or channel or "default"
        system_prompt = self.persona.system_prompt(self.memory.read())
        prompt = f"{context}\n\n{text}".strip() if context else text
        model = choose_model(
            text,
            self.persona.cfg.model,
            self.persona.cfg.model_light,
            dispatch=dispatch,
            broadcast=broadcast,
        )

        resume = self._resume_id(conversation)
        try:
            return await self._run(system_prompt, prompt, on_update, resume, conversation, channel, model)
        except Exception:
            # A resumed session id may be stale/missing (e.g. CLI session pruned).
            # Drop it and retry once from a fresh conversation rather than failing.
            if resume is not None:
                self._sessions.pop(conversation, None)
                return await self._run(system_prompt, prompt, on_update, None, conversation, channel, model)
            raise

    async def _run(
        self,
        system_prompt: str,
        prompt: str,
        on_update,
        resume: Optional[str],
        conversation: str,
        channel: Optional[str] = None,
        model: Optional[str] = None,
    ) -> str:
        model = model or self.persona.cfg.model
        options = self._options(system_prompt, resume, model)
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
                        self._remember_session(conversation, msg.session_id)
                    self._record_usage(msg, channel, model)
        return "\n\n".join(chunks).strip()

    def _record_usage(self, msg, channel: Optional[str], model: str) -> None:
        """Best-effort: log this turn's token spend to the audit log. Never let a
        usage-logging hiccup break the reply."""
        try:
            self.audit.record_usage(
                persona=self.persona.name,
                model=model,
                usage=getattr(msg, "usage", None),
                cost_usd=getattr(msg, "total_cost_usd", None),
                num_turns=getattr(msg, "num_turns", None),
                channel=channel,
            )
        except Exception:
            pass
