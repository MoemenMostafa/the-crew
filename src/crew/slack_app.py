"""One Slack Socket Mode app per persona.

The event→IncomingMessage translation and token resolution are pure functions so
they can be tested without opening a socket. The socket wiring itself is a thin
shell around slack-bolt's async app.
"""

from __future__ import annotations

import os
import re
from typing import Awaitable, Callable, Optional

from .config import PersonaConfig
from .router import IncomingMessage

_MENTION = re.compile(r"<@[A-Z0-9]+>")


def resolve_tokens(cfg: PersonaConfig) -> tuple[str, str]:
    bot = os.environ.get(cfg.bot_token_env)
    app = os.environ.get(cfg.app_token_env)
    missing = [name for name, val in ((cfg.bot_token_env, bot), (cfg.app_token_env, app)) if not val]
    if missing:
        raise RuntimeError(
            f"Missing Slack token(s) in environment for persona {cfg.name!r}: "
            f"{', '.join(missing)}. See .env.example."
        )
    return bot, app


def event_to_incoming(
    event: dict, persona_name: str, is_mention: bool = False, is_coordinator: bool = False
) -> Optional[IncomingMessage]:
    """Translate a Slack event into a turn, or None to ignore it.

    Routing policy:
      * ``app_mention`` (``is_mention=True``) → always handled — this persona was
        explicitly addressed, by a human or a teammate bot (that's how handoffs work).
      * plain message in a DM → handled (human 1:1).
      * plain message in a channel → ignored, *unless* this persona is the
        coordinator (``is_coordinator``), in which case unaddressed human questions
        are picked up to triage. Bot/agent channel chatter is always ignored.
    """
    if event.get("subtype"):  # edits, joins, channel_topic, bot_message, etc.
        return None

    text = _MENTION.sub("", event.get("text", "")).strip()
    if not text:
        return None

    channel = event.get("channel", "")
    is_dm = event.get("channel_type") == "im" or channel.startswith("D")
    from_agent = bool(event.get("bot_id"))
    dispatch = False

    if is_mention:
        # Reply at root in a DM; in a channel, thread under the message.
        thread = None if is_dm else (event.get("thread_ts") or event.get("ts"))
    elif is_dm and not from_agent:
        thread = None  # human DM
    elif is_coordinator and not from_agent:
        # Unaddressed human question in a channel → the coordinator triages it,
        # threaded under the question.
        thread = event.get("thread_ts") or event.get("ts")
        dispatch = True
    else:
        return None  # channel chatter / bot messages we weren't addressed in

    return IncomingMessage(
        persona=persona_name,
        channel=channel,
        thread=thread,
        text=text,
        sender=event.get("user", "unknown"),
        ts=event.get("ts"),
        from_agent=from_agent,
        dispatch=dispatch,
    )


OnMessage = Callable[[IncomingMessage], Awaitable[None]]


class SlackConnector:
    """Wires one persona's Slack app to the router. Built lazily so importing this
    module (and unit-testing the pure helpers) never requires live tokens."""

    def __init__(self, cfg: PersonaConfig, on_message: OnMessage, is_coordinator: bool = False):
        self.cfg = cfg
        self.on_message = on_message
        self.is_coordinator = is_coordinator
        self._app = None
        self._handler = None

    def _build(self):
        from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
        from slack_bolt.app.async_app import AsyncApp

        bot_token, app_token = resolve_tokens(self.cfg)
        app = AsyncApp(token=bot_token)
        name = self.cfg.name

        async def _route(event, is_mention):
            msg = event_to_incoming(
                event, name, is_mention=is_mention, is_coordinator=self.is_coordinator
            )
            if msg is not None:
                await self.on_message(msg)

        @app.event("app_mention")
        async def _on_mention(event, ack=None):  # pragma: no cover - needs live socket
            await _route(event, is_mention=True)

        @app.event("message")
        async def _on_message(event, ack=None):  # pragma: no cover - needs live socket
            await _route(event, is_mention=False)

        self._app = app
        self._handler = AsyncSocketModeHandler(app, app_token)

    async def start(self) -> None:  # pragma: no cover - needs live socket
        if self._handler is None:
            self._build()
        await self._handler.start_async()

    async def stop(self) -> None:  # pragma: no cover - needs live socket
        if self._handler is not None:
            await self._handler.close_async()

    async def post(self, channel: str, thread: Optional[str], text: str) -> None:  # pragma: no cover
        if self._app is None:
            self._build()
        await self._app.client.chat_postMessage(
            channel=channel, thread_ts=thread, text=text, link_names=True
        )

    async def react(self, channel: str, ts: str, emoji: str, add: bool) -> None:  # pragma: no cover
        """Add/remove a reaction as a working/done indicator. Best-effort: callers
        wrap this so a missing `reactions:write` scope never breaks a reply."""
        if self._app is None:
            self._build()
        if add:
            await self._app.client.reactions_add(channel=channel, timestamp=ts, name=emoji)
        else:
            await self._app.client.reactions_remove(channel=channel, timestamp=ts, name=emoji)
