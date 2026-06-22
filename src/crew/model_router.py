"""Pick the cheapest model that fits a request — no extra LLM call.

Each persona has a `model` (the capable, expensive default) and an optional
`model_light` (a cheaper model for low-stakes turns). Routing is heuristic: it
uses the structural signals we already have (coordinator triage, team broadcast)
plus a light-touch scan of the message text.

The bias is deliberate: when in doubt, pick heavy. A wrong *downgrade* costs
answer quality on real work; a wrong *upgrade* only costs some tokens. Downgrade
is also opt-in — with no `model_light` configured, every turn uses `model`.
"""

from __future__ import annotations

import re
from typing import Optional

# Substantive-work signals: a code block, something that looks like a filename,
# or a verb that implies real engineering/analysis work. Any hit ⇒ heavy model.
_HEAVY_RE = re.compile(
    r"```"
    r"|\b\w[\w./-]*\.(py|ts|tsx|js|jsx|go|rs|java|rb|sql|sh|ya?ml|json|md|css|html|toml)\b"
    r"|\b(implement|build|fix|debug|refactor|rewrite|deploy|release|"
    r"investigate|reproduce|design|architect|analy[sz]e|review|optimi[sz]e|"
    r"migrate|integrate|profile|benchmark|trace|diagnose|troubleshoot)\b",
    re.IGNORECASE,
)

# A message with no heavy signal and under this length is treated as light
# (a quick question, an ack, a short status check).
_LIGHT_MAX_CHARS = 280


def needs_heavy(text: str, *, dispatch: bool = False, broadcast: bool = False) -> bool:
    """True if this turn should use the heavy (capable) model."""
    text = text or ""
    # Triage (`dispatch`) and team broadcasts are short, low-stakes routing/answers
    # — keep them light *unless* the message itself clearly asks for real work.
    if dispatch or broadcast:
        return bool(_HEAVY_RE.search(text))
    if _HEAVY_RE.search(text):
        return True
    return len(text) > _LIGHT_MAX_CHARS


def choose_model(
    text: str,
    heavy: str,
    light: Optional[str],
    *,
    dispatch: bool = False,
    broadcast: bool = False,
) -> str:
    """Resolve a request to a concrete model id. Returns `heavy` whenever no
    distinct `light` model is configured (downgrade is opt-in)."""
    if not light or light == heavy:
        return heavy
    return heavy if needs_heavy(text, dispatch=dispatch, broadcast=broadcast) else light
