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


def event_to_incoming(event: dict, persona_name: str) -> Optional[IncomingMessage]:
    """Translate a Slack message/app_mention event. Returns None to ignore it."""
    # Ignore bot messages (avoid self-loops in Phase 1) and edits/joins/etc.
    if event.get("bot_id") or event.get("subtype"):
        return None

    text = _MENTION.sub("", event.get("text", "")).strip()
    if not text:
        return None

    channel = event.get("channel", "")
    # In a DM, reply at the root (no thread). In a channel, reply in-thread:
    # use the existing thread, or open one under the triggering message.
    is_dm = event.get("channel_type") == "im" or channel.startswith("D")
    thread = None if is_dm else (event.get("thread_ts") or event.get("ts"))

    return IncomingMessage(
        persona=persona_name,
        channel=channel,
        thread=thread,
        text=text,
        sender=event.get("user", "unknown"),
    )


OnMessage = Callable[[IncomingMessage], Awaitable[None]]


class SlackConnector:
    """Wires one persona's Slack app to the router. Built lazily so importing this
    module (and unit-testing the pure helpers) never requires live tokens."""

    def __init__(self, cfg: PersonaConfig, on_message: OnMessage):
        self.cfg = cfg
        self.on_message = on_message
        self._app = None
        self._handler = None

    def _build(self):
        from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
        from slack_bolt.app.async_app import AsyncApp

        bot_token, app_token = resolve_tokens(self.cfg)
        app = AsyncApp(token=bot_token)
        name = self.cfg.name

        async def _route(event):
            msg = event_to_incoming(event, name)
            if msg is not None:
                await self.on_message(msg)

        @app.event("app_mention")
        async def _on_mention(event, ack=None):  # pragma: no cover - needs live socket
            await _route(event)

        @app.event("message")
        async def _on_message(event, ack=None):  # pragma: no cover - needs live socket
            await _route(event)

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
            channel=channel, thread_ts=thread, text=text
        )
