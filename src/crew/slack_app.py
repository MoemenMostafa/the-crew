"""One Slack Socket Mode app per persona.

The event→IncomingMessage translation and token resolution are pure functions so
they can be tested without opening a socket. The socket wiring itself is a thin
shell around slack-bolt's async app.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
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


_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")
# A local image reference the agent can drop in a reply to attach a screenshot:
#   markdown image with a local path  ![caption](/abs/shot.png)   (http(s) skipped)
#   or an explicit marker             [[screenshot: /abs/shot.png]]
_MD_LOCAL_IMG = re.compile(r"!\[[^\]]*\]\(\s*(?!https?://)([^)\s]+)\s*\)")
_IMG_MARKER = re.compile(r"\[\[(?:screenshot|image)\s*:\s*([^\]]+?)\s*\]\]", re.IGNORECASE)


def extract_image_paths(text: str) -> tuple[str, list[str]]:
    """Pull local image paths out of a reply so they can be uploaded as files.

    Returns (clean_text, paths). Only local image paths (by extension) are taken;
    http(s) images and non-image links are left in the text untouched."""
    paths: list[str] = []

    def take(m):
        p = m.group(1).strip().strip("\"'")
        if p.lower().endswith(_IMG_EXT):
            paths.append(p)
            return ""  # strip the reference from the text
        return m.group(0)  # not an image → leave it

    text = _MD_LOCAL_IMG.sub(take, text)
    text = _IMG_MARKER.sub(take, text)
    clean = re.sub(r"[ \t]+\n", "\n", text)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, paths


def attachment_dir() -> Path:
    """Directory inbound Slack attachments are downloaded to (created on demand)."""
    d = Path(tempfile.gettempdir()) / "crew-attachments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def attachment_path(base: Path, file_obj: dict) -> Path:
    """Local destination for a downloaded Slack file. Namespaced by the file id so
    distinct uploads never collide, and the basename is sanitized to stay inside
    ``base`` (no path traversal from a hostile filename)."""
    fid = str(file_obj.get("id") or "file")
    name = os.path.basename(str(file_obj.get("name") or "")) or "attachment"
    return base / f"{fid}_{name}"


def sole_bot_in_thread(messages: list, my_bot_user_id: str) -> bool:
    """True if the ONLY bot that has posted in this thread is me.

    Lets a persona answer an untagged follow-up in a thread it already owns
    (`messages` = conversations_replies' messages; a bot message carries `bot_id`
    and its `user` is the bot's user id). Conservative: returns False if another
    bot posted, if I never posted, or if any bot message can't be attributed to a
    user id (ambiguous → require an explicit @mention rather than guess)."""
    if not my_bot_user_id:
        return False
    bot_users = set()
    for m in messages:
        if m.get("bot_id"):
            uid = m.get("user")
            if not uid:
                return False  # unattributable bot message → don't guess
            bot_users.add(uid)
    return bot_users == {my_bot_user_id}


def _strip_inline_md(s: str) -> str:
    """Flatten inline Markdown to plain text — used for table cells, which become
    monospace (a code block renders ``*x*`` / ``<url|label>`` literally, so we want
    the bare text instead)."""
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"(?<!\w)__(.+?)__(?!\w)", r"\1", s)
    s = re.sub(r"~~(.+?)~~", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1 (\2)", s)
    return s.strip()


def _tables_to_code_blocks(text: str) -> str:
    """Slack has no Markdown tables — they render as raw `| a | b |` lines. Convert
    each table to an aligned monospace code block (which Slack does render). Handles
    tables with or without leading/trailing pipes; cell contents are flattened to
    plain text since a code block renders Markdown literally."""
    lines = text.split("\n")

    def is_sep(s: str) -> bool:
        # A separator row: contains a pipe (distinguishes it from a `---` thematic
        # break) and every cell is dashes with optional alignment colons.
        s = s.strip()
        if "|" not in s:
            return False
        cells = [c.strip() for c in s.strip("|").split("|")]
        return bool(cells) and all(re.fullmatch(r":?-{1,}:?", c) for c in cells)

    def is_row(s: str) -> bool:
        # Any line carrying a pipe is a candidate row (outer pipes optional).
        return "|" in s.strip()

    def cells_of(s: str) -> list:
        return [_strip_inline_md(c) for c in s.strip().strip("|").split("|")]

    out: list = []
    i = 0
    while i < len(lines):
        # A table = a header row immediately followed by a `---` separator row.
        if (
            i + 1 < len(lines)
            and is_row(lines[i])
            and not is_sep(lines[i])
            and is_sep(lines[i + 1])
        ):
            rows = [cells_of(lines[i])]
            j = i + 2
            while j < len(lines) and is_row(lines[j]) and not is_sep(lines[j]):
                rows.append(cells_of(lines[j]))
                j += 1
            ncol = max(len(r) for r in rows)
            for r in rows:
                r += [""] * (ncol - len(r))
            widths = [max(len(r[c]) for r in rows) for c in range(ncol)]
            out.append("```")
            for r in rows:
                out.append(" | ".join(r[c].ljust(widths[c]) for c in range(ncol)).rstrip())
            out.append("```")
            i = j
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def _inline_mrkdwn(text: str) -> str:
    """Markdown → Slack mrkdwn for a span that is NOT inside a code fence."""
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


def to_slack_mrkdwn(text: str) -> str:
    """Convert standard Markdown (what the model writes) to Slack mrkdwn.

    Slack uses *bold* (not **bold**), _italic_, ~strike~ (not ~~), no #-headings,
    <url|label> links, and has no tables. Without this, replies show literal
    '**', '##', '| a | b |', etc.
    """
    # Tables first → monospace code block (Slack can't render Markdown tables).
    text = _tables_to_code_blocks(text)
    # Apply inline conversions only OUTSIDE fenced code blocks: splitting on ```
    # yields alternating outside/inside segments (even indices are outside). This
    # protects both model-authored code and the table blocks we just produced from
    # having their contents rewritten (which Slack would show literally).
    parts = text.split("```")
    for k in range(0, len(parts), 2):
        parts[k] = _inline_mrkdwn(parts[k])
    return "```".join(parts)


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
    # Drop noise subtypes (edits, joins, channel_topic, bot_message, …) but KEEP
    # file_share — a user attaching a file is a real message we should react/reply to.
    sub = event.get("subtype")
    if sub and sub != "file_share":
        return None

    raw = event.get("text", "")
    text = _MENTION.sub("", raw).strip()
    # A bare attachment (image dropped in with no caption) is still a real message;
    # only bail when there's neither text nor a file to act on.
    if not text and not event.get("files"):
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
    elif is_coordinator and not from_agent and not event.get("thread_ts"):
        # Unaddressed *new* human question in a channel → the coordinator triages it,
        # threaded under the question. Replies *inside* an existing thread are NOT
        # dispatched here — they're routed by thread participation (see
        # SlackConnector._maybe_thread_followup), so the bot already in the thread
        # answers without needing a tag.
        thread = event.get("ts")
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
        files=event.get("files") or [],
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
            # Untagged reply in a thread I'm the only bot in → answer without a tag.
            if msg is None and not is_mention:
                msg = await self._maybe_thread_followup(event)
            if msg is not None:
                if msg.files:
                    msg.file_paths = await self.download_files(msg.files)
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

    async def _maybe_thread_followup(self, event: dict):  # pragma: no cover - needs live socket
        """Route an untagged human reply in a thread I solely own, so I answer
        without a tag. Returns an IncomingMessage or None. Skips DMs, top-level
        (non-thread) messages, bots, and threads where I'm not the only bot."""
        sub = event.get("subtype")
        if (sub and sub != "file_share") or event.get("bot_id"):
            return None
        channel = event.get("channel", "")
        is_dm = event.get("channel_type") == "im" or channel.startswith("D")
        thread_ts = event.get("thread_ts")
        if is_dm or not thread_ts or not self.bot_user_id:
            return None
        text = _MENTION.sub("", event.get("text", "")).strip()
        files = event.get("files") or []
        if not text and not files:
            return None
        try:
            resp = await self._app.client.conversations_replies(
                channel=channel, ts=thread_ts, limit=200
            )
            messages = resp.get("messages", [])
        except Exception:
            return None
        if not sole_bot_in_thread(messages, self.bot_user_id):
            return None
        return IncomingMessage(
            persona=self.cfg.name, channel=channel, thread=thread_ts, text=text,
            sender=event.get("user", "unknown"), ts=event.get("ts"), from_agent=False,
            files=files,
        )

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

    async def download_files(self, files: list) -> list:  # pragma: no cover - needs live socket + files:read
        """Download inbound Slack attachments to local files so the agent can open
        them (Read renders images visually). `url_private` requires the bot token as
        a Bearer header. Best-effort: a file that fails to download is skipped, never
        breaking the turn. Requires the bot's `files:read` scope."""
        import aiohttp

        if not files:
            return []
        if self._app is None:
            self._build()
        token = self._app.client.token
        base = attachment_dir()
        paths: list = []
        async with aiohttp.ClientSession() as session:
            for f in files:
                url = f.get("url_private_download") or f.get("url_private")
                if not url:
                    continue
                dest = attachment_path(base, f)
                try:
                    async with session.get(
                        url, headers={"Authorization": f"Bearer {token}"}
                    ) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.read()
                    dest.write_bytes(data)
                    paths.append(str(dest))
                except Exception:
                    continue
        return paths

    async def stop(self) -> None:  # pragma: no cover - needs live socket
        if self._handler is not None:
            await self._handler.close_async()

    async def post(self, channel: str, thread: Optional[str], text: str) -> None:  # pragma: no cover
        if self._app is None:
            self._build()
        await self._app.client.chat_postMessage(
            channel=channel, thread_ts=thread, text=text, link_names=True
        )

    async def upload_files(
        self, channel: str, thread: Optional[str], paths: list, comment: str = ""
    ) -> bool:  # pragma: no cover - needs live socket + files:write
        """Upload local image files into the thread (screenshots from tests). The
        cleaned reply text rides along as the first file's comment. Returns True if
        at least one file went up. Requires the bot to have the `files:write` scope."""
        if self._app is None:
            self._build()
        sent = False
        for p in paths:
            await self._app.client.files_upload_v2(
                channel=channel, thread_ts=thread, file=p,
                title=os.path.basename(p),
                initial_comment=(comment or None) if not sent else None,
            )
            sent = True
        return sent

    async def react(self, channel: str, ts: str, emoji: str, add: bool) -> None:  # pragma: no cover
        """Add/remove a reaction as a working/done indicator. Best-effort: callers
        wrap this so a missing `reactions:write` scope never breaks a reply."""
        if self._app is None:
            self._build()
        if add:
            await self._app.client.reactions_add(channel=channel, timestamp=ts, name=emoji)
        else:
            await self._app.client.reactions_remove(channel=channel, timestamp=ts, name=emoji)
