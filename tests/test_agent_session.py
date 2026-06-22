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
        mcp_servers={"browser": {"type": "stdio", "command": "npx"}},
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
    assert sess.has_session("default") is True
    # First call resumes nothing.
    assert FakeClient.instances[0].options.resume is None

    out2 = asyncio.run(sess.ask("And the other one"))
    assert out2 == "On it."
    # Second call (same conversation) resumes the remembered session id.
    assert FakeClient.instances[1].options.resume == "sess-123"
    # The model and cwd flow through from config.
    assert FakeClient.instances[0].options.model == "claude-opus-4-8"
    assert str(FakeClient.instances[0].options.cwd) == str(tmp_path)
    # MCP servers (e.g. the browser) flow through to the SDK options.
    assert FakeClient.instances[0].options.mcp_servers == {
        "browser": {"type": "stdio", "command": "npx"}
    }


def test_usage_is_logged_to_audit(tmp_path):
    import json

    FakeClient.instances.clear()
    persona = make_persona(tmp_path)
    audit_path = tmp_path / "a.jsonl"
    audit = AuditLog(audit_path, clock=lambda: 0.0)
    memory = Memory(persona.cfg.dir / "memory")
    sess = AgentSession(persona, audit, memory, client_factory=FakeClient)

    asyncio.run(sess.ask("Fix the bug", channel="#adam-dev"))

    entries = [json.loads(l) for l in audit_path.read_text().splitlines()]
    usage = [e for e in entries if e.get("event") == "usage"]
    assert len(usage) == 1
    assert usage[0]["persona"] == "adam"
    assert usage[0]["model"] == "claude-opus-4-8"
    assert usage[0]["channel"] == "#adam-dev"
    assert usage[0]["num_turns"] == 1


def test_light_model_chosen_for_low_stakes_turns(tmp_path):
    FakeClient.instances.clear()
    persona = make_persona(tmp_path)
    persona.cfg.model_light = "claude-sonnet-4-6"
    audit = AuditLog(tmp_path / "a.jsonl", clock=lambda: 0.0)
    memory = Memory(persona.cfg.dir / "memory")
    sess = AgentSession(persona, audit, memory, client_factory=FakeClient)

    # A quick question downgrades to the light model.
    asyncio.run(sess.ask("any update on this?"))
    assert FakeClient.instances[-1].options.model == "claude-sonnet-4-6"

    # A real work request stays on the heavy model.
    asyncio.run(sess.ask("please fix the login crash"))
    assert FakeClient.instances[-1].options.model == "claude-opus-4-8"


def test_usage_logs_the_actually_chosen_model(tmp_path):
    import json

    FakeClient.instances.clear()
    persona = make_persona(tmp_path)
    persona.cfg.model_light = "claude-sonnet-4-6"
    audit_path = tmp_path / "a.jsonl"
    audit = AuditLog(audit_path, clock=lambda: 0.0)
    memory = Memory(persona.cfg.dir / "memory")
    sess = AgentSession(persona, audit, memory, client_factory=FakeClient)

    asyncio.run(sess.ask("quick q?"))
    usage = [
        json.loads(l) for l in audit_path.read_text().splitlines()
        if json.loads(l).get("event") == "usage"
    ]
    assert usage[-1]["model"] == "claude-sonnet-4-6"


def test_max_turns_flows_into_options_only_when_set(tmp_path):
    FakeClient.instances.clear()
    persona = make_persona(tmp_path)
    audit = AuditLog(tmp_path / "a.jsonl", clock=lambda: 0.0)
    memory = Memory(persona.cfg.dir / "memory")

    # Unset → SDK gets its default (no explicit cap).
    sess = AgentSession(persona, audit, memory, client_factory=FakeClient)
    asyncio.run(sess.ask("go"))
    assert getattr(FakeClient.instances[-1].options, "max_turns", None) in (None, 0)

    # Set → flows through to the options.
    persona.cfg.max_turns = 12
    asyncio.run(sess.ask("go again"))
    assert FakeClient.instances[-1].options.max_turns == 12


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

    # First "process": one turn in a thread saves the session id to disk.
    s1 = AgentSession(persona, audit, memory, client_factory=FakeClient, store=store)
    asyncio.run(s1.ask("hi", conversation="#t1"))
    assert store.get("adam", "#t1") == "sess-123"

    # Second "process" (fresh AgentSession, same store) resumes that thread's id.
    s2 = AgentSession(persona, audit, memory, client_factory=FakeClient, store=store)
    assert s2.has_session("#t1") is True
    asyncio.run(s2.ask("again", conversation="#t1"))
    assert FakeClient.instances[-1].options.resume == "sess-123"

    # A different thread for the same persona is independent (no cross-thread bleed).
    assert s2.has_session("#t2") is False


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
    store.set("adam", "#t1", "stale-id")

    sess = AgentSession(persona, audit, memory, client_factory=ResumeFailClient, store=store)
    out = asyncio.run(sess.ask("hello", conversation="#t1"))

    assert out == "fresh start"                 # recovered instead of erroring
    assert store.get("adam", "#t1") == "new-1"  # new id saved
    assert len(ResumeFailClient.instances) == 2  # one failed resume, one fresh


class AlwaysFailClient:
    """Fails on resume AND on a fresh run — exercises the stale-cleanup path."""

    def __init__(self, options=None, transport=None):
        self.options = options

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def query(self, prompt, session_id="default"):
        pass

    async def receive_response(self):
        raise RuntimeError("session not found")
        yield  # pragma: no cover - makes this an async generator


def test_stale_id_is_purged_from_store_even_when_retry_fails(tmp_path):
    import pytest

    persona = make_persona(tmp_path)
    audit = AuditLog(tmp_path / "a.jsonl", clock=lambda: 0.0)
    memory = Memory(persona.cfg.dir / "memory")
    store = SessionStore(tmp_path / "sessions.json")
    store.set("adam", "#t1", "stale-id")

    sess = AgentSession(persona, audit, memory, client_factory=AlwaysFailClient, store=store)
    with pytest.raises(RuntimeError):
        asyncio.run(sess.ask("hi", conversation="#t1"))

    # The stale id is gone from the persisted store, so it won't double-trip every
    # future turn (or resurrect after a restart).
    assert store.get("adam", "#t1") is None
