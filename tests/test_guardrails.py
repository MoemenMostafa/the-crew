import asyncio

from crew.audit import AuditLog
from crew.config import Guardrails
from crew.guardrails import make_can_use_tool


def decide(hook, tool_name, tool_input):
    return asyncio.run(hook(tool_name, tool_input, None))


def build(tmp_path, **overrides):
    params = dict(block_destructive=True, require_branch=True, protected_branches=["main", "master"])
    params.update(overrides)
    g = Guardrails(**params)
    audit = AuditLog(tmp_path / "a.jsonl", clock=lambda: 0.0)
    return make_can_use_tool("adam", g, audit)


def test_force_push_to_main_denied(tmp_path):
    hook = build(tmp_path)
    r = decide(hook, "Bash", {"command": "git push --force origin main"})
    assert r.behavior == "deny"


def test_rm_rf_denied(tmp_path):
    hook = build(tmp_path)
    assert decide(hook, "Bash", {"command": "rm -rf /"}).behavior == "deny"


def test_commit_on_protected_branch_denied_when_require_branch(tmp_path):
    hook = build(tmp_path)
    # No branch switch in the command; current branch is assumed protected unless
    # a feature branch was created — guardrail denies bare commits to be safe.
    r = decide(hook, "Bash", {"command": "git commit -m 'x'"})
    assert r.behavior == "deny"
    assert "branch" in r.message.lower()


def test_create_branch_allowed(tmp_path):
    hook = build(tmp_path)
    assert decide(hook, "Bash", {"command": "git checkout -b feat/x"}).behavior == "allow"


def test_commit_allowed_when_not_requiring_branch(tmp_path):
    hook = build(tmp_path, require_branch=False)
    assert decide(hook, "Bash", {"command": "git commit -m 'x'"}).behavior == "allow"


def test_normal_edit_allowed(tmp_path):
    hook = build(tmp_path)
    assert decide(hook, "Edit", {"file_path": "app/main.py"}).behavior == "allow"


def test_prod_deploy_denied(tmp_path):
    hook = build(tmp_path)
    r = decide(hook, "Bash", {"command": "docker compose -f docker-compose.prod.yml up -d"})
    assert r.behavior == "deny"
