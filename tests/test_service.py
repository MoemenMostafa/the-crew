import asyncio

from crew.config import CrewConfig, Guardrails, PersonaConfig
from crew.router import IncomingMessage
from crew.service import Crew


class FakeSession:
    def __init__(self, persona, audit, memory, store=None):
        self.calls = []
        self.store = store

    async def ask(self, text, context="", on_update=None):
        self.calls.append(text)
        return "done"


class FakeConnector:
    def __init__(self, cfg, on_message):
        self.cfg = cfg
        self.on_message = on_message
        self.started = False
        self.posts = []

    async def start(self):
        self.started = True

    async def stop(self):
        self.started = False

    async def post(self, channel, thread, text):
        self.posts.append(text)

    async def react(self, channel, ts, emoji, add):
        pass


def make_config(tmp_path, personality="Direct."):
    pdir = tmp_path / "personas" / "adam"
    (pdir / "memory").mkdir(parents=True)
    (pdir / "personality.md").write_text(personality)
    (pdir / "expertise.md").write_text("Python.")
    cfg = PersonaConfig(
        name="adam",
        display_name="Adam",
        role="Senior Developer",
        workdir=str(tmp_path),
        model="claude-opus-4-8",
        channels=["#adam-dev"],
        bot_token_env="ADAM_SLACK_BOT_TOKEN",
        app_token_env="ADAM_SLACK_APP_TOKEN",
        allowed_tools=[],
        guardrails=Guardrails(),
        dir=pdir,
    )
    return CrewConfig(personas=[cfg], audit_log=tmp_path / "audit.jsonl", root=tmp_path)


def test_start_wires_worker_and_dispatches(tmp_path):
    async def run():
        crew = Crew(
            make_config(tmp_path),
            session_factory=FakeSession,
            connector_factory=FakeConnector,
        )
        await crew.start()
        conn = crew.connectors["adam"]
        assert conn.started is True

        await conn.on_message(IncomingMessage("adam", "#adam-dev", None, "hello", "u"))
        await crew.router.join()
        # No text ack by default (the reaction is the indicator); just the reply.
        assert conn.posts == ["done"]
        assert crew.sessions["adam"].calls == ["hello"]

        await crew.stop()

    asyncio.run(run())


def test_kill_switch_pauses_and_resumes(tmp_path):
    async def run():
        crew = Crew(
            make_config(tmp_path),
            session_factory=FakeSession,
            connector_factory=FakeConnector,
        )
        await crew.start()
        conn = crew.connectors["adam"]

        # Control word pauses; a subsequent normal message is dropped.
        await conn.on_message(IncomingMessage("adam", "#adam-dev", None, "crew-stop", "u"))
        assert crew.router.paused is True
        await conn.on_message(IncomingMessage("adam", "#adam-dev", None, "do work", "u"))
        await asyncio.sleep(0.01)
        assert crew.sessions["adam"].calls == []  # dropped while paused

        # Resume re-enables dispatch.
        await conn.on_message(IncomingMessage("adam", "#adam-dev", None, "crew-resume", "u"))
        assert crew.router.paused is False
        await conn.on_message(IncomingMessage("adam", "#adam-dev", None, "now work", "u"))
        await crew.router.join()
        assert crew.sessions["adam"].calls == ["now work"]

        await crew.stop()

    asyncio.run(run())


def test_crew_reload_applies_edited_personality(tmp_path):
    async def run():
        crew = Crew(
            make_config(tmp_path, personality="Original voice."),
            session_factory=FakeSession,
            connector_factory=FakeConnector,
        )
        await crew.start()
        conn = crew.connectors["adam"]
        assert "Original voice." in crew.personas["adam"].system_prompt()

        # Edit the file on disk, then reload via the control word.
        (tmp_path / "personas" / "adam" / "personality.md").write_text("New voice.")
        await conn.on_message(IncomingMessage("adam", "#adam-dev", None, "crew-reload", "u"))

        sp = crew.personas["adam"].system_prompt()
        assert "New voice." in sp
        assert "Original voice." not in sp
        assert any("Reloaded" in p for p in conn.posts)
        await crew.stop()

    asyncio.run(run())
