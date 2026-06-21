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
_AT = re.compile(r"@([A-Za-z][A-Za-z0-9._-]*)")


def _broadcast_hit(raw: str, aliases) -> bool:
    """True if a human message addresses the whole team via any alias.

    Matches three forms Slack produces for `@team`-style text:
      * literal `@team` typed in the message,
      * Slack's special broadcasts `<!everyone>` / `<!channel>` / `<!here>`,
      * a user-group mention whose label is an alias: `<!subteam^ID|team>`.
    """
    if not aliases:
        return False
    low = raw.lower()
    for a in aliases:
        if re.search(rf"(?<![\w/])@{re.escape(a)}\b", low):
            return True
        if f"<!{a}>" in low or f"|{a}>" in low:
            return True
    return False


def _strip_broadcast(text: str, aliases) -> str:
    """Remove broadcast tokens so the agent doesn't see or parrot `@team`."""
    for a in aliases:
        text = re.sub(rf"(?<![\w/])@{re.escape(a)}\b", "", text, flags=re.IGNORECASE)
        text = re.sub(rf"<!{re.escape(a)}>", "", text, flags=re.IGNORECASE)
        text = re.sub(rf"<!subteam\^[A-Z0-9]+\|{re.escape(a)}>", "", text, flags=re.IGNORECASE)
    return text.strip()


def to_slack_mrkdwn(text: str) -> str:
    """Convert standard Markdown (what the model writes) to Slack mrkdwn.

    Slack uses *bold* (not **bold**), _italic_, ~strike~ (not ~~), no #-headings,
    and <url|label> links. Without this, replies show literal '**', '##', etc.
    """
    # Links [label](url) -> <url|label>  (do first, before * handling)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"<\2|\1>", text)
    # Headings: leading #..###### -> bold line
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$", r"*\1*", text)
    # Bold: **x** or __x__ -> *x*
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"(?<!\w)__(.+?)__(?!\w)", r"*\1*", text)
    # Strikethrough ~~x~~ -> ~x~
    text = re.sub(r"~~(.+?)~~", r"~\1~", text)
    return text


def rewrite_mentions(text: str, name_to_id: dict) -> str:
    """Turn '@Sara' into a real Slack mention '<@SARA_BOT_ID>' so the bot is pinged.

    `name_to_id` maps lowercased persona name/display-name → bot user id. Unknown
    @handles are left untouched. This is more reliable than chat.postMessage's
    `link_names`, which doesn't dependably link bot users.
    """
    if not name_to_id:
        return text

    def repl(m):
        uid = name_to_id.get(m.group(1).lower())
        return f"<@{uid}>" if uid else m.group(0)

    return _AT.sub(repl, text)


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
    event: dict,
    persona_name: str,
    is_mention: bool = False,
    is_coordinator: bool = False,
    broadcast_aliases=(),
) -> Optional[IncomingMessage]:
    """Translate a Slack event into a turn, or None to ignore it.

    Routing policy:
      * ``app_mention`` (``is_mention=True``) → always handled — this persona was
        explicitly addressed, by a human or a teammate bot (that's how handoffs work).
      * a human channel message addressing the whole team (`@team`, see
        ``broadcast_aliases``) → handled by EVERY persona, threaded under the message,
        so the team responds together (flagged ``broadcast`` so each answers for its
        own area). Bots can't trigger a broadcast (no fan-out loops).
      * plain message in a DM → handled (human 1:1).
      * plain message in a channel → ignored, *unless* this persona is the
        coordinator (``is_coordinator``), in which case unaddressed human questions
        are picked up to triage. Bot/agent channel chatter is always ignored.
    """
    if event.get("subtype"):  # edits, joins, channel_topic, bot_message, etc.
        return None

    raw = event.get("text", "")
    text = _MENTION.sub("", raw).strip()
    if not text:
        return None

    channel = event.get("channel", "")
    is_dm = event.get("channel_type") == "im" or channel.startswith("D")
    from_agent = bool(event.get("bot_id"))
    dispatch = False
    # A team broadcast only makes sense from a human in a channel (a DM is 1:1).
    broadcast = (
        not from_agent and not is_dm and _broadcast_hit(raw, broadcast_aliases)
    )

    if is_mention or broadcast:
        # Reply at root in a DM; in a channel, thread under the message.
        thread = None if is_dm else (event.get("thread_ts") or event.get("ts"))
        if broadcast:
            text = _strip_broadcast(text, broadcast_aliases) or text
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
        broadcast=broadcast,
    )


OnMessage = Callable[[IncomingMessage], Awaitable[None]]


class SlackConnector:
    """Wires one persona's Slack app to the router. Built lazily so importing this
    module (and unit-testing the pure helpers) never requires live tokens."""

    def __init__(
        self,
        cfg: PersonaConfig,
        on_message: OnMessage,
        is_coordinator: bool = False,
        broadcast_aliases=(),
    ):
        self.cfg = cfg
        self.on_message = on_message
        self.is_coordinator = is_coordinator
        self.broadcast_aliases = tuple(broadcast_aliases)
        self.bot_user_id = None  # resolved at start(); used for mention rewriting
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
                event, name, is_mention=is_mention, is_coordinator=self.is_coordinator,
                broadcast_aliases=self.broadcast_aliases,
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
        # Resolve our own bot user id so handoffs can be rewritten to real mentions.
        try:
            resp = await self._app.client.auth_test()
            self.bot_user_id = resp.get("user_id")
        except Exception:
            pass
        # connect_async() establishes the socket and RETURNS; start_async() would
        # block forever (await sleep(inf)), so the caller's post-startup code
        # (mention map, feedback, webhook) would never run. The process is kept
        # alive by run_forever()'s Event().wait().
        await self._handler.connect_async()

    async def fetch_thread(self, channel: str, thread_ts: str, limit: int = 200) -> list:  # pragma: no cover
        """Return the thread's messages as 'speaker: text' lines (best-effort)."""
        if self._app is None:
            self._build()
        resp = await self._app.client.conversations_replies(
            channel=channel, ts=thread_ts, limit=limit
        )
        lines = []
        for m in resp.get("messages", []):
            who = m.get("username") or ("teammate" if m.get("bot_id") else "user")
            txt = (m.get("text") or "").strip()
            if txt:
                lines.append(f"{who}: {txt}")
        return lines

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
