import asyncio

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from crew.agent_session import AgentSession
from crew.audit import AuditLog
from crew.config import Guardrails, PersonaConfig
from crew.memory import Memory
from crew.persona import Persona
from crew.state import SessionStore


class FakeClient:
    """Records the options it was built with and replays a scripted response."""

    instances = []

    def __init__(self, options=None, transport=None):
        self.options = options
        FakeClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def query(self, prompt, session_id="default"):
        self.prompt = prompt

    async def receive_response(self):
        yield AssistantMessage(content=[TextBlock(text="On it.")], model="m")
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="sess-123",
        )


def make_persona(tmp_path):
    pdir = tmp_path / "personas" / "adam"
    (pdir / "memory").mkdir(parents=True)
    (pdir / "personality.md").write_text("Direct.")
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
        allowed_tools=["Read", "Bash"],
        guardrails=Guardrails(),
        dir=pdir,
    )
    return Persona.load(cfg)


def test_ask_returns_text_and_resumes_session(tmp_path):
    FakeClient.instances.clear()
    persona = make_persona(tmp_path)
    audit = AuditLog(tmp_path / "a.jsonl", clock=lambda: 0.0)
    memory = Memory(persona.cfg.dir / "memory")

    sess = AgentSession(persona, audit, memory, client_factory=FakeClient)

    out1 = asyncio.run(sess.ask("Fix the bug"))
    assert out1 == "On it."
    assert sess.session_id == "sess-123"
    # First call resumes nothing.
    assert FakeClient.instances[0].options.resume is None

    out2 = asyncio.run(sess.ask("And the other one"))
    assert out2 == "On it."
    # Second call resumes the stored session id.
    assert FakeClient.instances[1].options.resume == "sess-123"
    # The model and cwd flow through from config.
    assert FakeClient.instances[0].options.model == "claude-opus-4-8"
    assert str(FakeClient.instances[0].options.cwd) == str(tmp_path)


def test_ask_streams_intermediate_messages(tmp_path):
    FakeClient.instances.clear()
    persona = make_persona(tmp_path)
    audit = AuditLog(tmp_path / "a.jsonl", clock=lambda: 0.0)
    memory = Memory(persona.cfg.dir / "memory")
    sess = AgentSession(persona, audit, memory, client_factory=FakeClient)

    updates = []

    async def collect(text):
        updates.append(text)

    out = asyncio.run(sess.ask("Fix it", on_update=collect))
    assert updates == ["On it."]  # interim message delivered live
    assert out == "On it."        # full transcript still returned


def test_session_id_persists_and_resumes_across_restart(tmp_path):
    FakeClient.instances.clear()
    persona = make_persona(tmp_path)
    audit = AuditLog(tmp_path / "a.jsonl", clock=lambda: 0.0)
    memory = Memory(persona.cfg.dir / "memory")
    store = SessionStore(tmp_path / "state" / "sessions.json")

    # First "process": one turn saves the session id to disk.
    s1 = AgentSession(persona, audit, memory, client_factory=FakeClient, store=store)
    asyncio.run(s1.ask("hi"))
    assert store.get("adam") == "sess-123"

    # Second "process" (fresh AgentSession, same store) resumes that id.
    s2 = AgentSession(persona, audit, memory, client_factory=FakeClient, store=store)
    assert s2.session_id == "sess-123"
    asyncio.run(s2.ask("again"))
    assert FakeClient.instances[-1].options.resume == "sess-123"


class ResumeFailClient:
    """Raises when asked to resume a (stale) session; succeeds fresh."""

    instances = []

    def __init__(self, options=None, transport=None):
        self.options = options
        ResumeFailClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def query(self, prompt, session_id="default"):
        pass

    async def receive_response(self):
        if self.options.resume is not None:
            raise RuntimeError("session not found")
        yield AssistantMessage(content=[TextBlock(text="fresh start")], model="m")
        yield ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="new-1",
        )


def test_stale_resume_falls_back_to_fresh_session(tmp_path):
    ResumeFailClient.instances.clear()
    persona = make_persona(tmp_path)
    audit = AuditLog(tmp_path / "a.jsonl", clock=lambda: 0.0)
    memory = Memory(persona.cfg.dir / "memory")
    store = SessionStore(tmp_path / "sessions.json")
    store.set("adam", "stale-id")

    sess = AgentSession(persona, audit, memory, client_factory=ResumeFailClient, store=store)
    out = asyncio.run(sess.ask("hello"))

    assert out == "fresh start"                 # recovered instead of erroring
    assert store.get("adam") == "new-1"         # new id saved
    assert len(ResumeFailClient.instances) == 2  # one failed resume, one fresh
