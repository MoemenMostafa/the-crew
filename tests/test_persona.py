from crew.config import Guardrails, PersonaConfig
from crew.persona import Persona


def make_cfg(tmp_path):
    pdir = tmp_path / "personas" / "adam"
    (pdir / "memory").mkdir(parents=True)
    (pdir / "personality.md").write_text("You are witty and direct.")
    (pdir / "expertise.md").write_text("You write Python.")
    return PersonaConfig(
        name="adam",
        display_name="Adam",
        role="Senior Developer",
        workdir=str(tmp_path),
        model="claude-opus-4-8",
        channels=["#adam-dev"],
        bot_token_env="ADAM_SLACK_BOT_TOKEN",
        app_token_env="ADAM_SLACK_APP_TOKEN",
        allowed_tools=["Read", "Bash"],
        guardrails=Guardrails(require_branch=True, protected_branches=["main"]),
        dir=pdir,
    )


def test_system_prompt_composes_personality_and_expertise(tmp_path):
    p = Persona.load(make_cfg(tmp_path))
    sp = p.system_prompt()
    assert "You are witty and direct." in sp
    assert "You write Python." in sp
    # Guardrail summary is surfaced so the agent knows the operating rules.
    assert "branch" in sp.lower()


def test_reload_picks_up_edited_personality(tmp_path):
    cfg = make_cfg(tmp_path)
    p = Persona.load(cfg)
    assert "witty" in p.system_prompt()

    (cfg.dir / "personality.md").write_text("You are calm and measured.")
    p.reload()
    sp = p.system_prompt()
    assert "calm and measured" in sp
    assert "witty" not in sp
