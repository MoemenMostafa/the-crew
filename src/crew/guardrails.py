"""Guardrail permission hook for the Claude Agent SDK.

Returns a ``can_use_tool(tool_name, tool_input, context)`` coroutine that the SDK
calls before every tool execution. It enforces, independent of the system prompt:

  * no force-push to a protected branch
  * no `rm -rf` of broad paths
  * no commit to a protected branch when ``require_branch`` is set
  * no production deploys

Everything else is allowed. Every decision is written to the audit log.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from .audit import AuditLog
from .config import Guardrails

# rm -rf against a broad/absolute/home path.
_RM_RF = re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*-?[a-zA-Z]*[rf][a-zA-Z]*\b")
_BROAD_PATH = re.compile(r"\s(/|~|/\*|\.\.|\$HOME)(\s|$)")

# git push ... --force/-f ... <protected>  (\b before a hyphen never matches, so
# anchor on whitespace/start instead).
_FORCE = re.compile(r"(?:^|\s)(--force(?:-with-lease)?|-f)\b")

_PROD_DEPLOY = re.compile(
    r"(docker[\s-]+compose.*prod|kubectl\s+apply|fly\s+deploy|"
    r"git\s+push\s+\w+\s+\w*:?refs/heads/(prod|production)|npm\s+publish)",
    re.IGNORECASE,
)


def _has_protected(command: str, protected: list[str]) -> bool:
    return any(re.search(rf"\b{re.escape(b)}\b", command) for b in protected)


def _is_destructive_rm(command: str) -> bool:
    return bool(_RM_RF.search(command) and _BROAD_PATH.search(command))


def _is_commit(command: str) -> bool:
    # A real `git commit ...` (not `git log --grep commit` etc.).
    return bool(re.search(r"\bgit\s+commit\b", command))


def _creates_branch(command: str) -> bool:
    return bool(
        re.search(r"\bgit\s+(checkout|switch)\s+-(b|c)\b", command)
        or re.search(r"\bgit\s+branch\s+\S", command)
    )


def make_can_use_tool(persona: str, g: Guardrails, audit: AuditLog):
    async def can_use_tool(tool_name: str, tool_input: dict[str, Any], context: Any):
        command = ""
        if tool_name == "Bash":
            command = str(tool_input.get("command", ""))

        deny = None

        if g.block_destructive and command:
            if _is_destructive_rm(command):
                deny = "Destructive `rm -rf` of a broad path is blocked."
            elif _FORCE.search(command) and "push" in command and _has_protected(
                command, g.protected_branches
            ):
                deny = "Force-pushing to a protected branch is blocked."
            elif _PROD_DEPLOY.search(command):
                deny = "Production deploys are blocked from agent sessions."

        if (
            deny is None
            and g.require_branch
            and command
            and _is_commit(command)
            and not _creates_branch(command)
        ):
            deny = (
                "Direct commits are blocked — create a feature branch first "
                f"(protected: {', '.join(g.protected_branches)}) and open a PR."
            )

        summary = command or _summarize_input(tool_input)
        if deny is not None:
            audit.record(persona, tool_name, summary, decision="deny")
            return PermissionResultDeny(message=deny)

        audit.record(persona, tool_name, summary, decision="allow")
        return PermissionResultAllow()

    return can_use_tool


def _summarize_input(tool_input: dict[str, Any]) -> str:
    for key in ("file_path", "path", "pattern", "url", "prompt"):
        if key in tool_input:
            return f"{key}={tool_input[key]}"
    try:
        return shlex.quote(str(tool_input))[:200]
    except Exception:  # pragma: no cover - defensive
        return "<unrepresentable>"
