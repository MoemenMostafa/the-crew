from pathlib import Path

import textwrap

from crew.config import load_config


def write_yaml(tmp_path: Path) -> Path:
    cfg = tmp_path / "crew.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            audit_log: .logs/audit.jsonl
            defaults:
              access_level: full
              autonomy: autonomous
              external_comms: gated
              protected_branches: [main, master]
              block_destructive: true
              require_branch: false
              model: claude-opus-4-8
              allowed_tools: [Read, Edit, Bash]
            personas:
              adam:
                display_name: Adam
                role: Senior Developer
                workdir: /tmp/repo
                channels: ["#adam-dev"]
                bot_token_env: ADAM_SLACK_BOT_TOKEN
                app_token_env: ADAM_SLACK_APP_TOKEN
                require_branch: true
                enabled: true
              eva:
                display_name: Eva
                role: Customer Support
                enabled: false
            """
        )
    )
    return cfg


def test_persona_override_and_default_fallback(tmp_path):
    cfg = load_config(write_yaml(tmp_path))

    adam = next(p for p in cfg.personas if p.name == "adam")
    # Per-persona override wins.
    assert adam.guardrails.require_branch is True
    # Falls back to global default.
    assert adam.guardrails.access_level == "full"
    assert adam.model == "claude-opus-4-8"
    assert adam.guardrails.protected_branches == ["main", "master"]
    assert adam.display_name == "Adam"
    assert adam.channels == ["#adam-dev"]
    assert adam.bot_token_env == "ADAM_SLACK_BOT_TOKEN"


def test_only_enabled_personas_loaded(tmp_path):
    cfg = load_config(write_yaml(tmp_path))
    names = {p.name for p in cfg.personas}
    assert names == {"adam"}  # eva is enabled: false


def test_persona_dir_resolves_under_personas(tmp_path):
    cfg = load_config(write_yaml(tmp_path))
    adam = cfg.personas[0]
    assert adam.dir == tmp_path / "personas" / "adam"
