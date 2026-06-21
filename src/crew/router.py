"""Routes incoming Slack messages to the right persona and serializes its turns.

Each persona gets its own asyncio queue and worker, so one persona's long task
never blocks another, while a single persona processes one turn at a time (no
working-tree clobbering). ``paused`` is the kill switch.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

log = logging.getLogger("crew.router")

_SLACK_ID = re.compile(r"<@([A-Z0-9]+)>")


def humanize_ids(text: str, names: dict) -> str:
    """Replace raw Slack mentions <@U123> with @Name (or @someone) so agents never
    see or parrot raw user IDs."""
    return _SLACK_ID.sub(lambda m: "@" + names.get(m.group(1), "someone"), text)

# post(persona, channel, thread, text) -> awaitable  (persona names the bot that replies)
PostFn = Callable[[str, str, Optional[str], str], Awaitable[None]]
# react(persona, channel, ts, emoji, add) -> awaitable  (working/done indicator)
ReactFn = Callable[[str, str, str, str, bool], Awaitable[None]]
# fetch_thread(persona, channel, thread_ts) -> awaitable[list[str]]  (transcript lines)
FetchThreadFn = Callable[[str, str, str], Awaitable[list]]

_WORKING = "eyes"
_DONE = "white_check_mark"


@dataclass
class IncomingMessage:
    persona: str
    channel: str
    thread: Optional[str]
    text: str
    sender: str
    ts: Optional[str] = None  # the triggering message's timestamp (for reactions)
    from_agent: bool = False  # sender is a teammate bot (for the loop-guard)
    dispatch: bool = False  # unaddressed channel question routed to the coordinator
    broadcast: bool = False  # addressed to the whole team (@team) — everyone replies


class Router:
    def __init__(
        self,
        sessions: dict,
        post: PostFn,
        ack_text: Optional[str] = None,
        react: Optional[ReactFn] = None,
        fetch_thread: Optional[FetchThreadFn] = None,
        names: Optional[dict] = None,
        operator: str = "the operator",
        max_agent_hops: int = 8,
    ):
        self.sessions = sessions
        self.post = post
        # Text acknowledgement; default None — the 👀/✅ reaction is the working
        # ("typing") indicator. Set a string to also post a worded ack.
        self.ack_text = ack_text
        self.react = react  # optional 👀/✅ working indicator
        self.fetch_thread = fetch_thread  # optional: read the thread for context
        self.names = names if names is not None else {}  # bot user id -> display name
        self.operator = operator  # label for the human (unknown senders)
        self.paused = False
        # Loop-guard: cap consecutive agent→agent hops without a human in between.
        self.max_agent_hops = max_agent_hops
        self._agent_hops = 0
        self._loop_notified = False
        self._queues: dict[str, asyncio.Queue] = {}
        self._workers: dict[str, asyncio.Task] = {}

    async def _safe_react(self, persona, channel, ts, emoji, add):
        if self.react is None or not ts:
            return
        try:
            await self.react(persona, channel, ts, emoji, add)
        except Exception:
            log.debug("reaction %s (%s) failed — continuing", emoji, "add" if add else "remove")

    async def handle(self, msg: IncomingMessage) -> None:
        if self.paused:
            log.info("paused — dropping message for %s", msg.persona)
            return
        if msg.persona not in self.sessions:
            log.warning("no session for persona %r — ignoring", msg.persona)
            return

        # Loop-guard: a human message resets the chain; agent→agent messages are
        # bounded so two personas can't ping-pong forever.
        if msg.from_agent:
            self._agent_hops += 1
            if self._agent_hops > self.max_agent_hops:
                if not self._loop_notified:
                    self._loop_notified = True
                    await self.post(
                        msg.persona,
                        msg.channel,
                        msg.thread,
                        ":vertical_traffic_light: Pausing agent-to-agent chatter — "
                        "hit the loop guard. A human can pick it back up.",
                    )
                log.warning("loop guard: dropping agent hop %d for %s", self._agent_hops, msg.persona)
                return
        else:
            self._agent_hops = 0
            self._loop_notified = False

        await self._queue(msg.persona).put(msg)

    def _queue(self, name: str) -> asyncio.Queue:
        if name not in self._queues:
            self._queues[name] = asyncio.Queue()
            self._workers[name] = asyncio.create_task(self._worker(name))
        return self._queues[name]

    async def _worker(self, name: str) -> None:
        queue = self._queues[name]
        session = self.sessions[name]
        while True:
            msg = await queue.get()
            try:
                # Working indicator: 👀 on the user's message while we work.
                await self._safe_react(name, msg.channel, msg.ts, _WORKING, True)
                # Immediate acknowledgement so the user knows we're on it.
                if self.ack_text:
                    await self.post(name, msg.channel, msg.thread, self.ack_text)

                # Stream the agent's intermediate messages as live progress.
                posted = False

                async def on_update(text, _name=name, _msg=msg):
                    nonlocal posted
                    posted = True
                    await self.post(_name, _msg.channel, _msg.thread, text)

                sender = self.names.get(msg.sender) or self.operator
                context = f"[Slack {msg.channel} — message from {sender}]"
                if msg.thread and self.fetch_thread is not None:
                    try:
                        lines = await self.fetch_thread(name, msg.channel, msg.thread)
                        if lines:
                            transcript = "\n".join(humanize_ids(l, self.names) for l in lines)
                            context = (
                                f"[Slack {msg.channel}] You've been brought into a thread. "
                                f"The conversation so far:\n{transcript}\n\n"
                                "Respond to the most recent message."
                            )
                    except Exception:
                        log.debug("thread fetch failed for %s — using minimal context", name)

                if msg.broadcast:
                    # Whole team was addressed (@team); every persona is replying in
                    # parallel. Keep each reply scoped so we don't get four near-identical
                    # answers — and stay silent if it's genuinely not your area.
                    context += (
                        "\n\n[This was sent to the WHOLE TEAM (@team) — every teammate is "
                        "replying at once. Answer only for YOUR area of ownership and keep it "
                        "short; don't repeat what another teammate would obviously cover. If it "
                        "truly isn't your area, a one-liner deferring to the right person is fine.]"
                    )

                reply = await session.ask(msg.text, context=context, on_update=on_update)

                # If the agent produced nothing along the way, post the final text
                # (or a fallback) so the turn always closes with a reply.
                if not posted:
                    await self.post(name, msg.channel, msg.thread, reply or "Done.")
                # Done: swap 👀 for ✅.
                await self._safe_react(name, msg.channel, msg.ts, _WORKING, False)
                await self._safe_react(name, msg.channel, msg.ts, _DONE, True)
            except Exception:  # keep the worker alive across a bad turn
                log.exception("turn failed for persona %s", name)
                await self._safe_react(name, msg.channel, msg.ts, _WORKING, False)
                try:
                    await self.post(
                        name, msg.channel, msg.thread, ":warning: I hit an error on that one."
                    )
                except Exception:
                    log.exception("failed to post error notice")
            finally:
                queue.task_done()

    async def join(self) -> None:
        """Wait until all currently-queued work is processed (used in tests)."""
        await asyncio.gather(*(q.join() for q in self._queues.values()))

    async def stop(self) -> None:
        for task in self._workers.values():
            task.cancel()
        for task in self._workers.values():
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._workers.clear()
        self._queues.clear()
