import asyncio

from crew.router import IncomingMessage, Router


class FakeSession:
    def __init__(self):
        self.calls = []

    async def ask(self, text, context=""):
        self.calls.append(text)
        return f"ok:{text}"


def test_messages_processed_in_order_and_posted(tmp_path):
    async def run():
        sess = FakeSession()
        posts = []

        async def post(channel, thread, text):
            posts.append((channel, thread, text))

        router = Router({"adam": sess}, post)
        await router.handle(IncomingMessage("adam", "#adam-dev", "t1", "first", "user"))
        await router.handle(IncomingMessage("adam", "#adam-dev", "t1", "second", "user"))
        await router.join()
        await router.stop()
        return sess.calls, posts

    calls, posts = asyncio.run(run())
    assert calls == ["first", "second"]
    assert [p[2] for p in posts] == ["ok:first", "ok:second"]


def test_paused_router_drops_dispatch(tmp_path):
    async def run():
        sess = FakeSession()
        posts = []

        async def post(channel, thread, text):
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


def test_unknown_persona_ignored(tmp_path):
    async def run():
        router = Router({}, lambda *a: None)
        await router.handle(IncomingMessage("ghost", "#x", None, "hi", "user"))
        await router.stop()

    asyncio.run(run())  # must not raise
