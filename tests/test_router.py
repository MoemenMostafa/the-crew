import asyncio

from crew.router import IncomingMessage, Router


class FakeSession:
    def __init__(self, stream=None):
        self.calls = []
        self.stream = stream  # optional list of interim updates to emit

    async def ask(self, text, context="", on_update=None):
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


def test_unknown_persona_ignored(tmp_path):
    async def run():
        router = Router({}, lambda *a: None)
        await router.handle(IncomingMessage("ghost", "#x", None, "hi", "user"))
        await router.stop()

    asyncio.run(run())  # must not raise
