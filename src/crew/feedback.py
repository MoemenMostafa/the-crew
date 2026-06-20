"""Portable, config-driven feedback feed.

A feedback *source* yields new items; the *poller* delivers each to a persona to
triage. Sources are pluggable and configured entirely from `crew.yaml`, so any
project can wire its feedback in without code changes:

  * ``sqlite`` — read-only against any SQLite DB, with a project-supplied SQL
    query (bind params ``:last_id`` / ``:limit``; alias columns to the canonical
    names: id, text, context, created_at, email, status).
  * ``http``   — GET a JSON endpoint (``{last_id}`` / ``{limit}`` substituted in
    the URL), navigate to the items array, and map fields. ``$VAR`` / ``${VAR}``
    in url/headers expand from the environment so tokens stay out of config.

Add a new source type by writing a class with ``fetch_since(last_id, limit)`` and
registering it in ``build_feedback_source``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

# Canonical fields every source maps onto. id must be a monotonic integer cursor.
_CANONICAL = ("id", "text", "context", "created_at", "email", "status")


@dataclass
class FeedbackItem:
    id: int
    text: str
    context: Optional[str] = None
    created_at: Optional[Any] = None
    email: Optional[str] = None
    status: str = "new"


def _to_item(d: dict) -> FeedbackItem:
    return FeedbackItem(
        id=int(d["id"]),
        text=str(d.get("text") or ""),
        context=d.get("context"),
        created_at=d.get("created_at"),
        email=d.get("email"),
        status=str(d.get("status") or "new"),
    )


class SqliteFeedbackSource:
    """Read-only SQLite source. The project supplies the query; we never write."""

    def __init__(self, db_path: str | Path, query: str):
        self.db_path = Path(db_path)
        self.query = query

    def fetch_since(self, last_id: int, limit: int = 50) -> list[FeedbackItem]:
        if not self.db_path.exists():
            raise FileNotFoundError(f"feedback DB not found at {self.db_path}")
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(self.query, {"last_id": last_id, "limit": limit}).fetchall()
        finally:
            con.close()
        return [_to_item(dict(r)) for r in rows]


def _dig(obj: Any, path: Optional[str]) -> Any:
    if not path:
        return obj
    for part in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj


class HttpFeedbackSource:
    """GET a JSON feedback endpoint and map its shape onto FeedbackItem."""

    def __init__(
        self,
        url: str,
        headers: Optional[dict] = None,
        items_path: Optional[str] = None,
        fields: Optional[dict] = None,
        timeout: float = 15.0,
    ):
        self.url = url
        self.headers = headers or {}
        self.items_path = items_path
        self.fields = fields or {}
        self.timeout = timeout

    def fetch_since(self, last_id: int, limit: int = 50) -> list[FeedbackItem]:
        url = os.path.expandvars(self.url).format(last_id=last_id, limit=limit)
        headers = {k: os.path.expandvars(v) for k, v in self.headers.items()}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (configured URL)
            data = json.load(resp)
        items = _dig(data, self.items_path)
        out: list[FeedbackItem] = []
        for raw in items or []:
            mapped = {c: _dig(raw, self.fields.get(c, c)) for c in _CANONICAL}
            out.append(_to_item(mapped))
        return out


def build_feedback_source(cfg: dict):
    """Construct a feedback source from a `crew.yaml` `feedback.source` block."""
    cfg = cfg or {}
    kind = cfg.get("type", "sqlite")
    if kind == "sqlite":
        return SqliteFeedbackSource(cfg["db_path"], cfg["query"])
    if kind == "http":
        return HttpFeedbackSource(
            url=cfg["url"],
            headers=cfg.get("headers"),
            items_path=cfg.get("items_path"),
            fields=cfg.get("fields"),
        )
    raise ValueError(f"unknown feedback source type: {kind!r} (expected 'sqlite' or 'http')")


# deliver(item) -> awaitable
DeliverFn = Callable[[FeedbackItem], Awaitable[None]]


class FeedbackPoller:
    """Polls the source for items newer than the last seen and delivers each.

    The last-seen id is persisted after **each** delivery, so a crash mid-batch
    never re-delivers everything. The (possibly blocking) source fetch runs in a
    thread so it doesn't stall the event loop.
    """

    def __init__(
        self,
        source,
        deliver: DeliverFn,
        state_path: str | Path,
        interval_seconds: float = 60.0,
    ):
        self.source = source
        self.deliver = deliver
        self.state_path = Path(state_path)
        self.interval_seconds = interval_seconds
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def last_id(self) -> int:
        try:
            return int(json.loads(self.state_path.read_text()).get("last_id", 0))
        except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
            return 0

    def _save(self, last_id: int) -> None:
        self.state_path.write_text(json.dumps({"last_id": last_id}))

    async def poll_once(self) -> int:
        last = self.last_id()
        loop = asyncio.get_event_loop()
        items = await loop.run_in_executor(None, self.source.fetch_since, last)
        for item in items:
            await self.deliver(item)
            self._save(item.id)
        return len(items)
