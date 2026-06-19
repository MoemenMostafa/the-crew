import pytest

from crew.config import Guardrails, PersonaConfig
from crew.slack_app import event_to_incoming, resolve_tokens


def cfg():
    return PersonaConfig(
        name="adam",
        display_name="Adam",
        role="Senior Developer",
        workdir="/tmp",
        model="claude-opus-4-8",
        channels=["#adam-dev"],
        bot_token_env="ADAM_SLACK_BOT_TOKEN",
        app_token_env="ADAM_SLACK_APP_TOKEN",
        allowed_tools=[],
        guardrails=Guardrails(),
        dir=None,
    )


def test_app_mention_translates_and_strips_mention():
    event = {
        "type": "app_mention",
        "channel": "C123",
        "ts": "111.1",
        "user": "U999",
        "text": "<@UBOT> please fix the TTS bug",
    }
    msg = event_to_incoming(event, "adam")
    assert msg.persona == "adam"
    assert msg.channel == "C123"
    assert msg.thread == "111.1"
    assert msg.sender == "U999"
    assert msg.text == "please fix the TTS bug"


def test_thread_reply_uses_thread_ts():
    event = {"channel": "C1", "ts": "222.2", "thread_ts": "111.1", "user": "U1", "text": "hi"}
    msg = event_to_incoming(event, "adam")
    assert msg.thread == "111.1"


def test_ignores_bot_and_subtype_events():
    assert event_to_incoming({"channel": "C1", "ts": "1", "bot_id": "B1", "text": "x"}, "adam") is None
    assert (
        event_to_incoming({"channel": "C1", "ts": "1", "subtype": "message_changed", "text": "x"}, "adam")
        is None
    )


def test_ignores_empty_text():
    assert event_to_incoming({"channel": "C1", "ts": "1", "user": "U1", "text": "   "}, "adam") is None


def test_resolve_tokens_reads_env(monkeypatch):
    monkeypatch.setenv("ADAM_SLACK_BOT_TOKEN", "xoxb-abc")
    monkeypatch.setenv("ADAM_SLACK_APP_TOKEN", "xapp-def")
    bot, app = resolve_tokens(cfg())
    assert bot == "xoxb-abc"
    assert app == "xapp-def"


def test_resolve_tokens_missing_raises(monkeypatch):
    monkeypatch.delenv("ADAM_SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ADAM_SLACK_APP_TOKEN", raising=False)
    with pytest.raises(RuntimeError) as e:
        resolve_tokens(cfg())
    assert "ADAM_SLACK_BOT_TOKEN" in str(e.value)
