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
from .feedback import FeedbackItem, FeedbackPoller, build_feedback_source
from .memory import Memory
from .persona import Persona
from .router import IncomingMessage, Router
from .slack_app import SlackConnector
from .state import SessionStore

log = logging.getLogger("crew.service")

_STOP_WORDS = {"/crew-stop", "crew-stop", "crew: stop"}
_RESUME_WORDS = {"/crew-resume", "crew-resume", "crew: resume"}
_RELOAD_WORDS = {"/crew-reload", "crew-reload", "crew: reload"}


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

        self.router = Router(self.sessions, self._post, react=self._react)

        for cfg in config.personas:
            self.connectors[cfg.name] = connector_factory(cfg, self._on_message)

        # Feedback feed → Eva (Phase 3). Only set up if enabled and its persona
        # is actually running.
        self.feedback_poller: Optional[FeedbackPoller] = None
        self._feedback_task = None
        fb = config.feedback
        if fb and fb.enabled and fb.persona in self.sessions:
            self.feedback_poller = FeedbackPoller(
                source=build_feedback_source(fb.source),
                deliver=self._deliver_feedback,
                state_path=config.root / "state" / "feedback.json",
                interval_seconds=fb.poll_interval_seconds,
            )
        elif fb and fb.enabled:
            log.warning(
                "feedback feed enabled but persona %r isn't running — skipping", fb.persona
            )

    async def _deliver_feedback(self, item: FeedbackItem) -> None:
        """Hand one new feedback item to the triage persona as a turn."""
        fb = self.config.feedback
        who = f" from {item.email}" if item.email else ""
        context = f"\n\nContext: {item.context}" if item.context else ""
        text = (
            f"New user feedback #{item.id}{who}:\n\n"
            f"{item.text}{context}\n\n"
            "Triage this: classify it (bug / feature request / praise / confusion), "
            "and if it needs action, @mention the right teammate in #crew-team. "
            "Draft a reply to the user (held for approval — don't send it)."
        )
        await self.router.handle(
            IncomingMessage(
                persona=fb.persona,
                channel=fb.channel,
                thread=None,
                text=text,
                sender="loquina-feedback",
                from_agent=False,
            )
        )

    async def _post(self, persona: str, channel: str, thread: Optional[str], text: str) -> None:
        await self.connectors[persona].post(channel, thread, text)

    async def _react(self, persona: str, channel: str, ts: str, emoji: str, add: bool) -> None:
        await self.connectors[persona].react(channel, ts, emoji, add)

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
        if low in _RELOAD_WORDS:
            for persona in self.personas.values():
                persona.reload()
            await self.connectors[msg.persona].post(
                msg.channel,
                msg.thread,
                ":arrows_counterclockwise: Reloaded personality/expertise for: "
                + ", ".join(self.personas)
                + ". (Applies on each persona's next turn. crew.yaml changes still need a restart.)",
            )
            return
        await self.router.handle(msg)

    async def start(self) -> None:
        names = ", ".join(self.connectors) or "(none)"
        log.info("starting Crew with personas: %s", names)
        await asyncio.gather(*(c.start() for c in self.connectors.values()))
        if self.feedback_poller is not None:
            log.info("starting Loquina feedback feed → %s", self.config.feedback.persona)
            self._feedback_task = asyncio.create_task(self._run_feedback_loop())

    async def _run_feedback_loop(self) -> None:  # pragma: no cover - long-running loop
        poller = self.feedback_poller
        while True:
            try:
                n = await poller.poll_once()
                if n:
                    log.info("surfaced %d new feedback item(s)", n)
            except Exception:
                log.exception("feedback poll failed — will retry")
            await asyncio.sleep(poller.interval_seconds)

    async def run_forever(self) -> None:  # pragma: no cover - long-running
        await self.start()
        try:
            await asyncio.Event().wait()
        finally:
            await self.stop()

    async def stop(self) -> None:
        if self._feedback_task is not None:
            self._feedback_task.cancel()
            try:
                await self._feedback_task
            except asyncio.CancelledError:
                pass
        await self.router.stop()
        await asyncio.gather(
            *(c.stop() for c in self.connectors.values()), return_exceptions=True
        )
