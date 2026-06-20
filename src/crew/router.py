"""Routes incoming Slack messages to the right persona and serializes its turns.

Each persona gets its own asyncio queue and worker, so one persona's long task
never blocks another, while a single persona processes one turn at a time (no
working-tree clobbering). ``paused`` is the kill switch.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

log = logging.getLogger("crew.router")

# post(persona, channel, thread, text) -> awaitable  (persona names the bot that replies)
PostFn = Callable[[str, str, Optional[str], str], Awaitable[None]]


@dataclass
class IncomingMessage:
    persona: str
    channel: str
    thread: Optional[str]
    text: str
    sender: str


class Router:
    def __init__(self, sessions: dict, post: PostFn, ack_text: Optional[str] = "🛠️ On it…"):
        self.sessions = sessions
        self.post = post
        self.ack_text = ack_text  # immediate acknowledgement; None disables it
        self.paused = False
        self._queues: dict[str, asyncio.Queue] = {}
        self._workers: dict[str, asyncio.Task] = {}

    async def handle(self, msg: IncomingMessage) -> None:
        if self.paused:
            log.info("paused — dropping message for %s", msg.persona)
            return
        if msg.persona not in self.sessions:
            log.warning("no session for persona %r — ignoring", msg.persona)
            return
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
                # Immediate acknowledgement so the user knows we're on it.
                if self.ack_text:
                    await self.post(name, msg.channel, msg.thread, self.ack_text)

                # Stream the agent's intermediate messages as live progress.
                posted = False

                async def on_update(text, _name=name, _msg=msg):
                    nonlocal posted
                    posted = True
                    await self.post(_name, _msg.channel, _msg.thread, text)

                context = f"[Slack {msg.channel} — message from {msg.sender}]"
                reply = await session.ask(msg.text, context=context, on_update=on_update)

                # If the agent produced nothing along the way, post the final text
                # (or a fallback) so the turn always closes with a reply.
                if not posted:
                    await self.post(name, msg.channel, msg.thread, reply or "Done.")
            except Exception:  # keep the worker alive across a bad turn
                log.exception("turn failed for persona %s", name)
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
