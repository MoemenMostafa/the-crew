import pytest

from crew.config import Guardrails, PersonaConfig
from crew.slack_app import (
    event_to_incoming,
    extract_image_paths,
    resolve_tokens,
    rewrite_mentions,
    sole_bot_in_thread,
    to_slack_mrkdwn,
)


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
    msg = event_to_incoming(event, "adam", is_mention=True)
    assert msg.persona == "adam"
    assert msg.channel == "C123"
    assert msg.thread == "111.1"
    assert msg.sender == "U999"
    assert msg.text == "please fix the TTS bug"
    assert msg.ts == "111.1"
    assert msg.from_agent is False


def test_mention_in_thread_uses_thread_ts():
    event = {"channel": "C1", "ts": "222.2", "thread_ts": "111.1", "user": "U1", "text": "<@U> hi"}
    msg = event_to_incoming(event, "adam", is_mention=True)
    assert msg.thread == "111.1"


def test_top_level_channel_mention_opens_thread():
    event = {"channel": "C1", "channel_type": "channel", "ts": "5.5", "user": "U1", "text": "<@U> hi"}
    assert event_to_incoming(event, "adam", is_mention=True).thread == "5.5"


def test_app_mention_from_teammate_marks_from_agent():
    # A teammate bot @mentioning this persona is a handoff — route it, flagged.
    event = {"channel": "C-crew", "ts": "9.9", "bot_id": "B-eva", "text": "<@U> can you fix this?"}
    msg = event_to_incoming(event, "adam", is_mention=True)
    assert msg is not None
    assert msg.from_agent is True


def test_dm_message_replies_at_root():
    event = {"channel": "D123", "channel_type": "im", "ts": "1.1", "user": "U1", "text": "hi"}
    msg = event_to_incoming(event, "adam", is_mention=False)
    assert msg is not None
    assert msg.thread is None


def test_dm_detected_by_channel_id_prefix():
    event = {"channel": "D999", "ts": "1.1", "user": "U1", "text": "hi"}
    assert event_to_incoming(event, "adam", is_mention=False) is not None


def test_channel_chatter_without_mention_is_ignored():
    event = {"channel": "C1", "channel_type": "channel", "ts": "1", "user": "U1", "text": "just talking"}
    assert event_to_incoming(event, "adam", is_mention=False) is None


def test_coordinator_picks_up_unaddressed_channel_question():
    event = {"channel": "C-crew", "channel_type": "channel", "ts": "9.9", "user": "U1",
             "text": "what should we build first?"}
    msg = event_to_incoming(event, "zakarya", is_mention=False, is_coordinator=True)
    assert msg is not None
    assert msg.dispatch is True
    assert msg.thread == "9.9"  # threaded under the question


def test_coordinator_ignores_bot_chatter():
    # Even the coordinator ignores non-mention messages from bots (no dispatch loops).
    event = {"channel": "C-crew", "ts": "9.9", "bot_id": "B-eva", "text": "fyi"}
    assert event_to_incoming(event, "zakarya", is_mention=False, is_coordinator=True) is None


def test_non_coordinator_still_ignores_unaddressed_channel_question():
    event = {"channel": "C-crew", "channel_type": "channel", "ts": "9.9", "user": "U1", "text": "hey"}
    assert event_to_incoming(event, "adam", is_mention=False, is_coordinator=False) is None


_ALIASES = ("team", "all", "crew", "everyone", "channel", "here")


def test_broadcast_from_human_in_channel_is_handled_by_every_persona():
    # "@team ..." from a human → each persona handles it, flagged broadcast, threaded.
    event = {"channel": "C-crew", "channel_type": "channel", "ts": "7.7", "user": "U1",
             "text": "@team standup in 5?"}
    msg = event_to_incoming(event, "adam", is_mention=False, broadcast_aliases=_ALIASES)
    assert msg is not None
    assert msg.broadcast is True
    assert msg.dispatch is False
    assert msg.thread == "7.7"          # threaded under the broadcast
    assert msg.text == "standup in 5?"  # the @team token is stripped


def test_broadcast_slack_special_channel_token():
    # Slack renders @channel as <!channel> — also a broadcast.
    event = {"channel": "C1", "channel_type": "channel", "ts": "1.1", "user": "U1",
             "text": "<!channel> ship it today"}
    msg = event_to_incoming(event, "sara", is_mention=False, broadcast_aliases=_ALIASES)
    assert msg is not None and msg.broadcast is True


def test_broadcast_ignored_from_a_bot():
    # A teammate bot writing "@team" must NOT fan out (no broadcast loops).
    event = {"channel": "C-crew", "ts": "1.1", "bot_id": "B-eva", "text": "@team done"}
    assert event_to_incoming(event, "adam", is_mention=False, broadcast_aliases=_ALIASES) is None


def test_broadcast_takes_precedence_over_coordinator_dispatch():
    # The coordinator broadcasts as itself rather than triaging the @team message.
    event = {"channel": "C-crew", "channel_type": "channel", "ts": "2.2", "user": "U1",
             "text": "@team what's the plan?"}
    msg = event_to_incoming(event, "zakarya", is_mention=False, is_coordinator=True,
                            broadcast_aliases=_ALIASES)
    assert msg.broadcast is True
    assert msg.dispatch is False


def test_broadcast_in_dm_is_just_a_normal_dm():
    # A DM is 1:1 — "@team" there isn't a fan-out.
    event = {"channel": "D1", "channel_type": "im", "ts": "1.1", "user": "U1", "text": "@team hi"}
    msg = event_to_incoming(event, "adam", is_mention=False, broadcast_aliases=_ALIASES)
    assert msg is not None
    assert msg.broadcast is False
    assert msg.thread is None


def test_no_aliases_means_no_broadcast():
    # With broadcast disabled (no aliases), "@team" is just ignored channel chatter.
    event = {"channel": "C1", "channel_type": "channel", "ts": "1", "user": "U1", "text": "@team hey"}
    assert event_to_incoming(event, "adam", is_mention=False, broadcast_aliases=()) is None


# --- untagged thread follow-up: answer in a thread you solely own --------------

def test_coordinator_does_not_dispatch_a_thread_reply():
    # An untagged reply *inside* a thread isn't a new question — the coordinator
    # leaves it to thread-participation routing, so it doesn't hijack the thread.
    event = {"channel": "C-crew", "channel_type": "channel", "ts": "9.9", "thread_ts": "1.1",
             "user": "U1", "text": "and the timeline?"}
    assert event_to_incoming(event, "zakarya", is_mention=False, is_coordinator=True) is None


def test_sole_bot_only_me():
    msgs = [
        {"user": "U-human"},
        {"bot_id": "B-adam", "user": "U-ADAM"},
        {"user": "U-human"},
    ]
    assert sole_bot_in_thread(msgs, "U-ADAM") is True


def test_sole_bot_false_when_another_bot_posted():
    msgs = [{"bot_id": "B-adam", "user": "U-ADAM"}, {"bot_id": "B-eva", "user": "U-EVA"}]
    assert sole_bot_in_thread(msgs, "U-ADAM") is False


def test_sole_bot_false_when_i_never_posted():
    msgs = [{"bot_id": "B-eva", "user": "U-EVA"}, {"user": "U-human"}]
    assert sole_bot_in_thread(msgs, "U-ADAM") is False


def test_sole_bot_false_on_unattributable_bot_message():
    # A bot message with no resolvable user id → don't guess, require a tag.
    msgs = [{"bot_id": "B-adam"}]
    assert sole_bot_in_thread(msgs, "U-ADAM") is False


def test_sole_bot_false_without_my_id():
    assert sole_bot_in_thread([{"bot_id": "B", "user": "U"}], "") is False


def test_ignores_subtype_events():
    assert (
        event_to_incoming({"channel": "C1", "ts": "1", "subtype": "message_changed", "text": "x"}, "adam")
        is None
    )


def test_ignores_empty_text():
    assert event_to_incoming(
        {"channel": "D1", "channel_type": "im", "ts": "1", "user": "U1", "text": "   "}, "adam"
    ) is None


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


def test_rewrite_mentions_links_known_names():
    mapping = {"sara": "USARA", "adam": "UADAM", "zakarya": "UZAK"}
    out = rewrite_mentions("@Sara this is yours, cc @adam and @nobody", mapping)
    assert "<@USARA>" in out
    assert "<@UADAM>" in out
    assert "@nobody" in out  # unknown handle left untouched


def test_rewrite_mentions_empty_map_is_noop():
    assert rewrite_mentions("@Sara hi", {}) == "@Sara hi"


def test_to_slack_mrkdwn_converts_markdown():
    assert to_slack_mrkdwn("**End nudge:** hi") == "*End nudge:* hi"
    assert to_slack_mrkdwn("## Subhead") == "*Subhead*"
    assert to_slack_mrkdwn("~~old~~") == "~old~"
    assert to_slack_mrkdwn("see [docs](https://x.test/y)") == "see <https://x.test/y|docs>"
    # already-Slack single-asterisk bold is left alone
    assert to_slack_mrkdwn("*keep*") == "*keep*"


def test_to_slack_mrkdwn_converts_table_to_code_block():
    md = "Results:\n| Name | Role |\n| --- | --- |\n| Adam | Dev |\n| Eva | Support |\nDone."
    out = to_slack_mrkdwn(md)
    assert "```" in out                      # wrapped in a monospace block
    assert "---" not in out                   # separator row dropped
    assert "Adam | Dev" in out                # aligned row rendered
    assert "Name | Role" in out
    assert out.startswith("Results:")         # surrounding text preserved
    assert out.rstrip().endswith("Done.")


def test_to_slack_mrkdwn_leaves_non_tables_alone():
    assert to_slack_mrkdwn("just a | pipe in text") == "just a | pipe in text"


def test_extract_image_markdown_local_path():
    clean, paths = extract_image_paths("Shipped:\n![Heute](/tmp/shots/heute.png)\nLooks good.")
    assert paths == ["/tmp/shots/heute.png"]
    assert "![" not in clean and "Shipped:" in clean and "Looks good." in clean


def test_extract_image_marker_and_ignores_remote():
    clean, paths = extract_image_paths("a [[screenshot: /tmp/a.jpg]] b ![x](https://h/y.png)")
    assert paths == ["/tmp/a.jpg"]        # local marker taken, remote left
    assert "https://h/y.png" in clean


def test_file_share_message_is_kept():
    # A user attaching a file (subtype file_share) with a caption + mention is a real turn.
    event = {"channel": "C1", "channel_type": "channel", "ts": "1.1", "user": "U1",
             "subtype": "file_share", "text": "<@U> look at this"}
    msg = event_to_incoming(event, "adam", is_mention=True)
    assert msg is not None and msg.text == "look at this"


def test_other_subtypes_still_dropped():
    event = {"channel": "C1", "ts": "1", "subtype": "message_changed", "text": "x"}
    assert event_to_incoming(event, "adam", is_mention=True) is None
