"""Configuration loading for the Crew.

A single ``crew.yaml`` holds global ``defaults`` plus per-persona entries; each
persona inherits the defaults and may override any of them. Nothing about access
level, autonomy, or guardrails is hardcoded — it all comes from here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Keys that live inside Guardrails and may be set at either the defaults level or
# per-persona level.
_GUARDRAIL_KEYS = (
    "access_level",
    "autonomy",
    "external_comms",
    "protected_branches",
    "block_destructive",
    "require_branch",
)


@dataclass
class Guardrails:
    access_level: str = "full"  # full | propose | sandboxed
    autonomy: str = "autonomous"  # autonomous | approve_handoffs | direct_only
    external_comms: str = "gated"  # gated | autonomous | readonly
    protected_branches: list[str] = field(default_factory=lambda: ["main", "master"])
    block_destructive: bool = True
    require_branch: bool = True


@dataclass
class PersonaConfig:
    name: str
    display_name: str
    role: str
    workdir: str
    model: str
    channels: list[str]
    bot_token_env: str
    app_token_env: str
    allowed_tools: list[str]
    guardrails: Guardrails
    dir: Path


@dataclass
class FeedbackConfig:
    enabled: bool
    persona: str
    channel: str
    poll_interval_seconds: float
    source: dict  # portable source spec (type: sqlite|http + its settings)


@dataclass
class WebhookConfig:
    enabled: bool
    host: str
    port: int
    secret_env: str
    persona: str
    channel: str


@dataclass
class DispatchConfig:
    enabled: bool
    coordinator: str


@dataclass
class CrewConfig:
    personas: list[PersonaConfig]
    audit_log: Path
    root: Path
    feedback: "FeedbackConfig | None" = None
    webhook: "WebhookConfig | None" = None
    dispatch: "DispatchConfig | None" = None


def _merge(defaults: dict, override: dict, key, fallback=None):
    if key in override:
        return override[key]
    if key in defaults:
        return defaults[key]
    return fallback


def load_config(path: str | Path) -> CrewConfig:
    path = Path(path)
    root = path.parent
    raw = yaml.safe_load(path.read_text()) or {}
    defaults = raw.get("defaults", {}) or {}
    personas_raw = raw.get("personas", {}) or {}

    personas: list[PersonaConfig] = []
    for name, entry in personas_raw.items():
        entry = entry or {}
        if not entry.get("enabled", False):
            continue

        guardrails = Guardrails(
            **{k: _merge(defaults, entry, k, getattr(Guardrails(), k)) for k in _GUARDRAIL_KEYS}
        )

        personas.append(
            PersonaConfig(
                name=name,
                display_name=entry.get("display_name", name.title()),
                role=entry.get("role", ""),
                workdir=str(_merge(defaults, entry, "workdir", "")),
                model=str(_merge(defaults, entry, "model", "claude-opus-4-8")),
                channels=list(_merge(defaults, entry, "channels", []) or []),
                bot_token_env=entry.get("bot_token_env", f"{name.upper()}_SLACK_BOT_TOKEN"),
                app_token_env=entry.get("app_token_env", f"{name.upper()}_SLACK_APP_TOKEN"),
                allowed_tools=list(_merge(defaults, entry, "allowed_tools", []) or []),
                guardrails=guardrails,
                dir=root / "personas" / name,
            )
        )

    audit_log = root / str(raw.get("audit_log", ".logs/audit.jsonl"))

    feedback = None
    fb = raw.get("feedback")
    if fb:
        feedback = FeedbackConfig(
            enabled=bool(fb.get("enabled", False)),
            persona=str(fb.get("persona", "eva")),
            channel=str(fb.get("channel", "#loquina-feedback")),
            poll_interval_seconds=float(fb.get("poll_interval_seconds", 60)),
            source=fb.get("source") or {},
        )

    webhook = None
    wh = raw.get("webhook")
    if wh:
        webhook = WebhookConfig(
            enabled=bool(wh.get("enabled", False)),
            host=str(wh.get("host", "127.0.0.1")),
            port=int(wh.get("port", 8787)),
            secret_env=str(wh.get("secret_env", "CREW_WEBHOOK_SECRET")),
            persona=str(wh.get("persona", "eva")),
            channel=str(wh.get("channel", "#loquina-feedback")),
        )

    dispatch = None
    dp = raw.get("dispatch")
    if dp:
        dispatch = DispatchConfig(
            enabled=bool(dp.get("enabled", False)),
            coordinator=str(dp.get("coordinator", "")),
        )

    return CrewConfig(
        personas=personas,
        audit_log=audit_log,
        root=root,
        feedback=feedback,
        webhook=webhook,
        dispatch=dispatch,
    )
