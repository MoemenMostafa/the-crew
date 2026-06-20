"""Wires config → personas → sessions → Slack connectors → one shared Router.

Also implements the kill switch: a `crew-stop` / `crew-resume` control message in
any channel pauses or resumes all dispatch.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .agent_session import AgentSession
from .audit import AuditLog
from .config import CrewConfig
from .memory import Memory
from .persona import Persona
from .router import IncomingMessage, Router
from .slack_app import SlackConnector
from .state import SessionStore

log = logging.getLogger("crew.service")

_STOP_WORDS = {"/crew-stop", "crew-stop", "crew: stop"}
_RESUME_WORDS = {"/crew-resume", "crew-resume", "crew: resume"}


class Crew:
    def __init__(
        self,
        config: CrewConfig,
        *,
        session_factory=AgentSession,
        connector_factory=SlackConnector,
    ):
        self.config = config
        self.audit = AuditLog(config.audit_log)
        self.store = SessionStore(config.root / "state" / "sessions.json")
        self.personas: dict[str, Persona] = {}
        self.sessions: dict[str, object] = {}
        self.connectors: dict[str, object] = {}

        for cfg in config.personas:
            persona = Persona.load(cfg)
            memory = Memory(cfg.dir / "memory")
            self.personas[cfg.name] = persona
            self.sessions[cfg.name] = session_factory(
                persona, self.audit, memory, store=self.store
            )

        self.router = Router(self.sessions, self._post)

        for cfg in config.personas:
            self.connectors[cfg.name] = connector_factory(cfg, self._on_message)

    async def _post(self, persona: str, channel: str, thread: Optional[str], text: str) -> None:
        await self.connectors[persona].post(channel, thread, text)

    async def _on_message(self, msg: IncomingMessage) -> None:
        low = msg.text.strip().lower()
        if low in _STOP_WORDS:
            self.router.paused = True
            await self.connectors[msg.persona].post(
                msg.channel, msg.thread, ":octagonal_sign: Crew paused. Say `crew-resume` to continue."
            )
            return
        if low in _RESUME_WORDS:
            self.router.paused = False
            await self.connectors[msg.persona].post(
                msg.channel, msg.thread, ":white_check_mark: Crew resumed."
            )
            return
        await self.router.handle(msg)

    async def start(self) -> None:
        names = ", ".join(self.connectors) or "(none)"
        log.info("starting Crew with personas: %s", names)
        await asyncio.gather(*(c.start() for c in self.connectors.values()))

    async def run_forever(self) -> None:  # pragma: no cover - long-running
        await self.start()
        try:
            await asyncio.Event().wait()
        finally:
            await self.stop()

    async def stop(self) -> None:
        await self.router.stop()
        await asyncio.gather(
            *(c.stop() for c in self.connectors.values()), return_exceptions=True
        )
