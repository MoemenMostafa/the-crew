"""Wires config → personas → sessions → Slack connectors → one shared Router.

Also implements the kill switch: a `crew-stop` / `crew-resume` control message in
any channel pauses or resumes all dispatch.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from .agent_session import AgentSession
from .audit import AuditLog
from .config import CrewConfig
from .feedback import FeedbackItem, FeedbackPoller, build_feedback_source
from .memory import Memory
from .persona import Persona
from .router import IncomingMessage, Router
from .slack_app import SlackConnector, rewrite_mentions, to_slack_mrkdwn
from .state import SessionStore
from .webhook import WebhookServer

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

        self.mention_map: dict = {}  # lowercased name/display-name -> bot user id
        self.id_to_name: dict = {}  # bot user id -> display name (for humanizing context)
        self.router = Router(
            self.sessions,
            self._post,
            react=self._react,
            fetch_thread=self._fetch_thread,
            names=self.id_to_name,
            operator=config.operator,
        )

        dp = config.dispatch
        coordinator = dp.coordinator if (dp and dp.enabled) else None
        for cfg in config.personas:
            self.connectors[cfg.name] = connector_factory(
                cfg, self._on_message, is_coordinator=(cfg.name == coordinator)
            )

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

        # Inbound webhook (push) — any project can POST feedback here.
        self.webhook_server: Optional[WebhookServer] = None
        wh = config.webhook
        if wh and wh.enabled:
            secret = os.environ.get(wh.secret_env)
            if not secret:
                log.warning(
                    "webhook enabled but %s is unset — not starting the webhook", wh.secret_env
                )
            elif wh.persona not in self.sessions:
                log.warning(
                    "webhook enabled but persona %r isn't running — not starting", wh.persona
                )
            else:
                self.webhook_server = WebhookServer(
                    host=wh.host, port=wh.port, secret=secret, handle=self._deliver_webhook
                )

    async def _route_feedback(self, item: FeedbackItem, persona: str, channel: str) -> None:
        """Hand one feedback item to a triage persona as a turn (shared by poller + webhook)."""
        if persona not in self.sessions:
            log.warning("feedback target persona %r isn't running — dropping item", persona)
            return
        who = f" from {item.email}" if item.email else ""
        context = f"\n\nContext: {item.context}" if item.context else ""
        ident = f" #{item.id}" if item.id else ""
        text = (
            f"New user feedback{ident}{who}:\n\n"
            f"{item.text}{context}\n\n"
            "Triage this: classify it (bug / feature request / praise / confusion), "
            "and if it needs action, @mention the right teammate in #crew-team. "
            "Draft a reply to the user (held for approval — don't send it)."
        )
        await self.router.handle(
            IncomingMessage(
                persona=persona,
                channel=channel,
                thread=None,
                text=text,
                sender="feedback",
                from_agent=False,
            )
        )

    async def _deliver_feedback(self, item: FeedbackItem) -> None:
        fb = self.config.feedback
        await self._route_feedback(item, fb.persona, fb.channel)

    async def _deliver_webhook(self, item: FeedbackItem, persona, channel) -> None:
        wh = self.config.webhook
        await self._route_feedback(item, persona or wh.persona, channel or wh.channel)

    async def _post(self, persona: str, channel: str, thread: Optional[str], text: str) -> None:
        text = to_slack_mrkdwn(text)  # **bold**/##/links -> Slack's mrkdwn
        text = rewrite_mentions(text, self.mention_map)  # @Sara -> <@BOT_ID> so handoffs ping
        await self.connectors[persona].post(channel, thread, text)

    async def _react(self, persona: str, channel: str, ts: str, emoji: str, add: bool) -> None:
        await self.connectors[persona].react(channel, ts, emoji, add)

    async def _fetch_thread(self, persona: str, channel: str, thread_ts: str) -> list:
        return await self.connectors[persona].fetch_thread(channel, thread_ts)

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
        if msg.dispatch:
            roster = "; ".join(
                f"{p.cfg.display_name} ({p.cfg.role})"
                for name, p in self.personas.items()
                if name != msg.persona
            )
            msg.text = (
                "(Posted in a shared channel addressed to no one — you're the coordinator.) "
                "Your job here is to ROUTE, not to answer everything yourself. Decide who "
                "owns this and hand it to them by @mentioning them — default to the "
                "specialist. Only answer directly when it is squarely your own area "
                "(product priorities, roadmap, scope, marketing/positioning). When in "
                "doubt, hand off rather than answer.\n\n"
                f"Teammates and what they own: {roster}.\n\n"
                "Respond with a short handoff: @mention the right teammate(s) and say in "
                "one line what you need from them — don't solve it for them. "
                f"The message:\n{msg.text}"
            )
        await self.router.handle(msg)

    async def start(self) -> None:
        names = ", ".join(self.connectors) or "(none)"
        log.info("starting Crew with personas: %s", names)
        await asyncio.gather(*(c.start() for c in self.connectors.values()))

        # Build the @name -> bot-user-id map so handoffs become real mentions.
        for name, conn in self.connectors.items():
            uid = getattr(conn, "bot_user_id", None)
            if uid:
                display = self.personas[name].cfg.display_name
                self.mention_map[name.lower()] = uid
                self.mention_map[display.lower()] = uid
                self.id_to_name[uid] = display  # reverse: for humanizing context/transcripts
        log.info("mention map resolved for: %s", ", ".join(sorted(set(self.mention_map))))

        if self.feedback_poller is not None:
            log.info("starting feedback poller → %s", self.config.feedback.persona)
            self._feedback_task = asyncio.create_task(self._run_feedback_loop())
        if self.webhook_server is not None:
            wh = self.config.webhook
            log.info("starting webhook on http://%s:%d/feedback → %s", wh.host, wh.port, wh.persona)
            await self.webhook_server.start()

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
        if self.webhook_server is not None:
            await self.webhook_server.stop()
        await self.router.stop()
        await asyncio.gather(
            *(c.stop() for c in self.connectors.values()), return_exceptions=True
        )
