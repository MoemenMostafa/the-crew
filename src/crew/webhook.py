"""Secure inbound webhook so any project can push feedback to the crew.

Complements the pull-based poller (`feedback.py`): instead of the crew reading a
project's DB, a project POSTs items here and they're routed to a triage persona.

Security: a shared secret (constant-time compared) in `Authorization: Bearer …`
or `X-Crew-Token`. The secret comes from the environment, never config. Binds to
127.0.0.1 by default — to accept remote calls, put it behind a TLS-terminating
reverse proxy and set the host explicitly.
"""

from __future__ import annotations

import hmac
from typing import Awaitable, Callable, Optional

from aiohttp import web

from .feedback import FeedbackItem

# handle(item, persona_override, channel_override) -> awaitable
HandleFn = Callable[[FeedbackItem, Optional[str], Optional[str]], Awaitable[None]]


def extract_token(headers) -> Optional[str]:
    auth = headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer ") :].strip()
    return headers.get("X-Crew-Token")


def token_ok(secret: Optional[str], provided: Optional[str]) -> bool:
    if not secret or not provided:
        return False
    return hmac.compare_digest(secret, provided)


def payload_to_item(data: dict) -> FeedbackItem:
    text = str((data or {}).get("text") or "").strip()
    if not text:
        raise ValueError("missing 'text'")
    return FeedbackItem(
        id=int(data.get("id") or 0),
        text=text,
        context=data.get("context"),
        created_at=data.get("created_at"),
        email=data.get("email"),
        status="new",
    )


class WebhookServer:
    def __init__(self, host: str, port: int, secret: Optional[str], handle: HandleFn):
        self.host = host
        self.port = port
        self.secret = secret
        self.handle = handle
        self._runner: Optional[web.AppRunner] = None

        self.app = web.Application()
        self.app.router.add_post("/feedback", self._on_feedback)
        self.app.router.add_get("/healthz", self._on_health)

    async def _on_health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def _on_feedback(self, request: web.Request) -> web.Response:
        if not token_ok(self.secret, extract_token(request.headers)):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        try:
            item = payload_to_item(data)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)

        await self.handle(item, data.get("persona"), data.get("channel"))
        return web.json_response({"ok": True}, status=202)

    async def start(self) -> None:  # pragma: no cover - binds a real socket
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()

    async def stop(self) -> None:  # pragma: no cover
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
