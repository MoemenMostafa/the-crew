import asyncio
import json
import sqlite3

import pytest

from crew.feedback import (
    FeedbackItem,
    FeedbackPoller,
    HttpFeedbackSource,
    SqliteFeedbackSource,
    build_feedback_source,
)

# Canonical query a project supplies — aliases columns to the canonical names.
QUERY = """
SELECT f.id AS id, f.text AS text, f.context AS context,
       f.created_at AS created_at, a.email AS email,
       COALESCE(f.status, 'new') AS status
FROM feedback f LEFT JOIN accounts a ON a.id = f.account_id
WHERE f.id > :last_id ORDER BY f.id ASC LIMIT :limit
"""


def make_db(tmp_path):
    path = tmp_path / "app.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE accounts (id INTEGER PRIMARY KEY, email TEXT);
        CREATE TABLE feedback (
            id INTEGER PRIMARY KEY, account_id INTEGER, text TEXT, context TEXT,
            created_at INTEGER, status TEXT DEFAULT 'new'
        );
        INSERT INTO accounts (id, email) VALUES (1, 'user@example.com');
        INSERT INTO feedback (id, account_id, text, context, created_at)
            VALUES (1, 1, 'TTS cuts off', 'bakery', 1000);
        INSERT INTO feedback (id, account_id, text, context, created_at)
            VALUES (2, NULL, 'Love it!', NULL, 2000);
        """
    )
    con.commit()
    con.close()
    return path


def test_sqlite_source_fetches_and_maps(tmp_path):
    src = SqliteFeedbackSource(make_db(tmp_path), QUERY)
    items = src.fetch_since(0)
    assert [i.id for i in items] == [1, 2]
    assert items[0].text == "TTS cuts off"
    assert items[0].email == "user@example.com"
    assert items[1].email is None


def test_sqlite_source_filters_by_last_id(tmp_path):
    src = SqliteFeedbackSource(make_db(tmp_path), QUERY)
    assert [i.id for i in src.fetch_since(1)] == [2]


def test_missing_db_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        SqliteFeedbackSource(tmp_path / "nope.db", QUERY).fetch_since(0)


def test_build_source_sqlite_and_unknown(tmp_path):
    src = build_feedback_source({"type": "sqlite", "db_path": str(make_db(tmp_path)), "query": QUERY})
    assert isinstance(src, SqliteFeedbackSource)
    with pytest.raises(ValueError):
        build_feedback_source({"type": "carrier-pigeon"})


def test_http_source_maps_fields_and_substitutes(monkeypatch):
    captured = {}

    class FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(self._payload).encode()

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        payload = {"feedback": [{"id": 7, "message": "hi", "user": {"email": "a@b.c"}}]}
        return FakeResp(payload)

    monkeypatch.setenv("FEEDBACK_API_TOKEN", "secret-xyz")
    monkeypatch.setattr("crew.feedback.urllib.request.urlopen", fake_urlopen)
    # json.load reads from the response object.
    monkeypatch.setattr("crew.feedback.json.load", lambda r: json.loads(r.read()))

    src = HttpFeedbackSource(
        url="https://x.test/api?since={last_id}",
        headers={"Authorization": "Bearer ${FEEDBACK_API_TOKEN}"},
        items_path="feedback",
        fields={"id": "id", "text": "message", "email": "user.email"},
    )
    items = src.fetch_since(5)
    assert captured["url"] == "https://x.test/api?since=5"      # {last_id} substituted
    assert captured["auth"] == "Bearer secret-xyz"             # $VAR expanded from env
    assert items[0].id == 7 and items[0].text == "hi" and items[0].email == "a@b.c"


def test_poller_delivers_new_and_persists(tmp_path):
    src = SqliteFeedbackSource(make_db(tmp_path), QUERY)
    delivered = []

    async def deliver(item: FeedbackItem):
        delivered.append(item.id)

    poller = FeedbackPoller(src, deliver, tmp_path / "state" / "feedback.json", interval_seconds=0)
    assert asyncio.run(poller.poll_once()) == 2
    assert delivered == [1, 2]
    assert poller.last_id() == 2
    assert asyncio.run(poller.poll_once()) == 0  # nothing new
