import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from crew.webhook import WebhookServer, extract_token, payload_to_item, token_ok


def test_token_ok_constant_time_semantics():
    assert token_ok("s3cret", "s3cret") is True
    assert token_ok("s3cret", "wrong") is False
    assert token_ok("s3cret", None) is False
    assert token_ok(None, "anything") is False
    assert token_ok("", "") is False


def test_extract_token_bearer_and_header():
    assert extract_token({"Authorization": "Bearer abc"}) == "abc"
    assert extract_token({"X-Crew-Token": "xyz"}) == "xyz"
    assert extract_token({}) is None


def test_payload_to_item_maps_and_requires_text():
    item = payload_to_item({"text": "crash", "email": "u@x.com", "id": 5, "context": "login"})
    assert item.id == 5 and item.text == "crash" and item.email == "u@x.com"
    with pytest.raises(ValueError):
        payload_to_item({"email": "u@x.com"})  # no text


def test_webhook_rejects_without_secret_and_accepts_with():
    async def run():
        calls = []

        async def handle(item, persona, channel):
            calls.append((item, persona, channel))

        server = WebhookServer("127.0.0.1", 0, "sekret", handle)
        async with TestClient(TestServer(server.app)) as client:
            r_unauth = await client.post("/feedback", json={"text": "hi"})
            r_badtok = await client.post(
                "/feedback", json={"text": "hi"}, headers={"Authorization": "Bearer nope"}
            )
            r_ok = await client.post(
                "/feedback",
                json={"text": "crash on login", "email": "u@x.com", "persona": "adam"},
                headers={"Authorization": "Bearer sekret"},
            )
            r_notext = await client.post(
                "/feedback", json={"email": "u@x.com"}, headers={"Authorization": "Bearer sekret"}
            )
            return (
                r_unauth.status,
                r_badtok.status,
                r_ok.status,
                r_notext.status,
                calls,
            )

    unauth, badtok, ok, notext, calls = asyncio.run(run())
    assert unauth == 401
    assert badtok == 401
    assert ok == 202
    assert notext == 400
    assert len(calls) == 1
    item, persona, channel = calls[0]
    assert item.text == "crash on login"
    assert item.email == "u@x.com"
    assert persona == "adam"  # payload override passed through
