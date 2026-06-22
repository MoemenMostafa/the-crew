from crew.model_router import choose_model, needs_heavy

HEAVY = "claude-opus-4-8"
LIGHT = "claude-sonnet-4-6"


def test_short_question_is_light():
    assert needs_heavy("what's the status on the login work?") is False
    assert choose_model("hey, any update?", HEAVY, LIGHT) == LIGHT


def test_dispatch_and_broadcast_are_light_by_default():
    assert needs_heavy("who can take this?", dispatch=True) is False
    assert needs_heavy("quick heads up team", broadcast=True) is False
    assert choose_model("thoughts?", HEAVY, LIGHT, dispatch=True) == LIGHT


def test_dev_verbs_force_heavy():
    for msg in (
        "can you fix the login crash?",
        "implement the export endpoint",
        "please refactor the auth module",
        "investigate why checkout 500s",
    ):
        assert needs_heavy(msg) is True, msg
        assert choose_model(msg, HEAVY, LIGHT) == HEAVY


def test_code_block_and_filename_force_heavy():
    assert needs_heavy("see ```def f(): pass```") is True
    assert needs_heavy("the bug is in server/index.ts near the top") is True


def test_work_request_overrides_dispatch_and_broadcast():
    # Even an unaddressed/broadcast message asking for real work goes heavy.
    assert needs_heavy("fix the failing deploy", dispatch=True) is True
    assert needs_heavy("can someone debug the crash?", broadcast=True) is True


def test_long_message_goes_heavy():
    assert needs_heavy("x " * 200) is True


def test_downgrade_is_opt_in():
    # No light model configured → always heavy, regardless of the message.
    assert choose_model("quick question", HEAVY, None) == HEAVY
    assert choose_model("quick question", HEAVY, HEAVY) == HEAVY
