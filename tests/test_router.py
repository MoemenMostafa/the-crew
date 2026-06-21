import asyncio

from crew.router import IncomingMessage, Router


class FakeSession:
    def __init__(self, stream=None, sessions=None):
        self.calls = []
        self.stream = stream  # optional list of interim updates to emit
        self._sessions = set(sessions or ())  # conversations we already "know"

    def has_session(self, conversation):
        return conversation in self._sessions

    async def ask(self, text, context="", on_update=None, channel=None, conversation=None, dispatch=False, broadcast=False):
        self.calls.append(text)
        if self.stream and on_update is not None:
            for u in self.stream:
                await on_update(u)
            return "\n\n".join(self.stream)
        return f"ok:{text}"


def test_messages_processed_in_order_and_posted(tmp_path):
    async def run():
        sess = FakeSession()
        posts = []

        async def post(persona, channel, thread, text):
            posts.append((persona, channel, thread, text))

        router = Router({"adam": sess}, post, ack_text=None)
        await router.handle(IncomingMessage("adam", "#adam-dev", "t1", "first", "user"))
        await router.handle(IncomingMessage("adam", "#adam-dev", "t1", "second", "user"))
        await router.join()
        await router.stop()
        return sess.calls, posts

    calls, posts = asyncio.run(run())
    assert calls == ["first", "second"]
    assert [p[3] for p in posts] == ["ok:first", "ok:second"]
    assert all(p[0] == "adam" for p in posts)


def test_paused_router_drops_dispatch(tmp_path):
    async def run():
        sess = FakeSession()
        posts = []

        async def post(persona, channel, thread, text):
            posts.append(text)

        router = Router({"adam": sess}, post)
        router.paused = True
        await router.handle(IncomingMessage("adam", "#adam-dev", None, "hi", "user"))
        await asyncio.sleep(0.01)
        await router.stop()
        return sess.calls, posts

    calls, posts = asyncio.run(run())
    assert calls == []
    assert posts == []


def test_immediate_ack_then_streamed_updates(tmp_path):
    async def run():
        sess = FakeSession(stream=["Looking into it…", "Found it — here's the fix."])
        posts = []

        async def post(persona, channel, thread, text):
            posts.append(text)

        router = Router({"adam": sess}, post, ack_text="🛠️ On it…")
        await router.handle(IncomingMessage("adam", "#adam-dev", "t1", "do a big task", "user"))
        await router.join()
        await router.stop()
        return posts

    posts = asyncio.run(run())
    # Immediate ack first, then each interim update, and no duplicate final.
    assert posts == ["🛠️ On it…", "Looking into it…", "Found it — here's the fix."]


def test_working_indicator_reactions(tmp_path):
    async def run():
        sess = FakeSession()
        reacts = []

        async def post(persona, channel, thread, text):
            pass

        async def react(persona, channel, ts, emoji, add):
            reacts.append((emoji, add))

        router = Router({"adam": sess}, post, ack_text=None, react=react)
        await router.handle(
            IncomingMessage("adam", "#adam-dev", "1.1", "do it", "user", ts="1.1")
        )
        await router.join()
        await router.stop()
        return reacts

    reacts = asyncio.run(run())
    # 👀 added at start, then removed and replaced with ✅ when done.
    assert reacts == [("eyes", True), ("eyes", False), ("white_check_mark", True)]


def test_no_reactions_without_ts(tmp_path):
    async def run():
        sess = FakeSession()
        reacts = []

        async def post(persona, channel, thread, text):
            return None

        async def react(persona, channel, ts, emoji, add):
            reacts.append(emoji)

        router = Router({"adam": sess}, post, ack_text=None, react=react)
        # ts=None (e.g. a synthetic message) -> no reaction attempts.
        await router.handle(IncomingMessage("adam", "#c", None, "hi", "user"))
        await router.join()
        await router.stop()
        return reacts

    assert asyncio.run(run()) == []


def test_loop_guard_bounds_agent_chatter(tmp_path):
    async def run():
        sess = FakeSession()
        posts = []

        async def post(persona, channel, thread, text):
            posts.append(text)

        router = Router({"adam": sess}, post, ack_text=None, max_agent_hops=3)
        # 4 consecutive agent-originated messages: first 3 run, 4th is dropped.
        for i in range(4):
            await router.handle(
                IncomingMessage("adam", "#crew-team", None, f"hop {i}", "eva", from_agent=True)
            )
        await router.join()
        await router.stop()
        return sess.calls, posts

    calls, posts = asyncio.run(run())
    assert calls == ["hop 0", "hop 1", "hop 2"]  # 4th dropped by the guard
    assert any("loop guard" in p for p in posts)  # one-time notice posted


def test_human_message_resets_loop_guard(tmp_path):
    async def run():
        sess = FakeSession()

        async def post(persona, channel, thread, text):
            pass

        router = Router({"adam": sess}, post, ack_text=None, max_agent_hops=2)
        # Trip the guard with agent hops, then a human speaks → counter resets.
        for i in range(3):
            await router.handle(IncomingMessage("adam", "#c", None, f"a{i}", "eva", from_agent=True))
        await router.handle(IncomingMessage("adam", "#c", None, "human here", "U1", from_agent=False))
        await router.handle(IncomingMessage("adam", "#c", None, "after reset", "eva", from_agent=True))
        await router.join()
        await router.stop()
        return sess.calls

    calls = asyncio.run(run())
    assert "human here" in calls
    assert "after reset" in calls  # agent chatter works again post-reset


def test_unknown_persona_ignored(tmp_path):
    async def run():
        router = Router({}, lambda *a: None)
        await router.handle(IncomingMessage("ghost", "#x", None, "hi", "user"))
        await router.stop()

    asyncio.run(run())  # must not raise


def test_thread_transcript_passed_as_context(tmp_path):
    class CtxSession:
        def __init__(self):
            self.contexts = []

        def has_session(self, conversation):
            return False  # brand-new thread → transcript should be injected

        async def ask(self, text, context="", on_update=None, channel=None, conversation=None, dispatch=False, broadcast=False):
            self.contexts.append(context)
            return "ok"

    async def run():
        sess = CtxSession()

        async def post(persona, channel, thread, text):
            pass

        async def fetch_thread(persona, channel, thread_ts):
            return ["user: improve the landing UX?", "zakarya: @sara this is yours"]

        router = Router({"adam": sess}, post, fetch_thread=fetch_thread)
        await router.handle(IncomingMessage("adam", "#crew-team", "9.9", "do it", "u", ts="9.9"))
        await router.join()
        await router.stop()
        return sess.contexts

    contexts = asyncio.run(run())
    assert "brought into a thread" in contexts[0]
    assert "improve the landing UX" in contexts[0]


def test_transcript_not_refetched_once_session_exists(tmp_path):
    # When the persona already has a session for the thread, the router must NOT
    # re-fetch the Slack transcript — the resumed session carries the context.
    async def run():
        # Session already "knows" thread 9.9.
        sess = FakeSession(sessions={"9.9"})
        fetched = []

        async def post(persona, channel, thread, text):
            pass

        async def fetch_thread(persona, channel, thread_ts):
            fetched.append(thread_ts)
            return ["user: earlier message"]

        router = Router({"adam": sess}, post, fetch_thread=fetch_thread)
        await router.handle(IncomingMessage("adam", "#crew-team", "9.9", "follow up", "u", ts="9.10"))
        await router.join()
        await router.stop()
        return fetched

    fetched = asyncio.run(run())
    assert fetched == []  # transcript fetch skipped


def test_humanize_ids_replaces_raw_mentions():
    from crew.router import humanize_ids
    names = {"U0BBVG428US": "Zakarya"}
    assert humanize_ids("<@U0BBVG428US> ping <@UNKNOWN1>", names) == "@Zakarya ping @someone"
