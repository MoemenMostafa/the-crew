# The Crew 🤖

A team of AI coworkers — **Adam** (Senior Developer), **Eva** (Customer Support),
**Zakarya** (Product Owner), **Sara** (Designer) — that you direct through Slack and
that collaborate with each other in Slack. They operate the **Loquina** product.

Each persona is a [Claude Agent SDK](https://pypi.org/project/claude-agent-sdk/)
session with its own character, expertise, memory, Slack identity, and guardrails.
The runtime is one always-on Python process using Slack **Socket Mode** (no inbound
URL — works behind a corporate proxy).

> **Status:** Phase 1 ships the full spine with **Adam** only, built so adding the
> rest of the team (Phase 2) is configuration, not new code. See
> `docs/superpowers/specs/` for the design and `docs/superpowers/plans/` for the plan.

## How it works

```
You ──Slack @Adam──▶ Crew (Socket Mode) ──▶ Adam's Agent SDK session
                                            ├─ tools run in the Loquina repo
                                            │  (every call gated by guardrails + audited)
                                            └─ reply posts back to the thread as Adam
```

- **Config-driven** — `crew.yaml` sets each persona's access level, autonomy,
  guardrails, model, channels, and working dir. Change a level, reload — no code edit.
- **Editable personality** — `personas/<name>/personality.md` (+ `expertise.md`)
  compose the system prompt. Edit and it re-characterizes on the next session.
- **Persistent memory** — `personas/<name>/memory/` survives restarts; read at session
  start, updated by the agent as it learns.
- **Full hands-on guardrails** — Adam works on feature branches → PRs (never `main`);
  `rm -rf`, force-push to protected branches, and prod deploys are blocked; every tool
  action is logged to `.logs/audit.jsonl`.
- **Kill switch** — post `crew-stop` (or `crew-resume`) in any channel, or Ctrl+C.

## Setup

### 1. Python + dependencies

```bash
cd crew
python3.10 -m venv .venv
.venv/bin/pip install -e ".[dev]"          # behind a proxy: PIP_CERT=/…/plauda/ca-bundle.pem
```

The Agent SDK drives your **authenticated `claude` CLI** — no `ANTHROPIC_API_KEY`
needed as long as `claude` is logged in.

### 2. Create the Slack apps (one per persona)

Phase 1 needs only **Adam**. In the **loquina** Slack workspace, at
<https://api.slack.com/apps> → *Create New App* → *From scratch* (name it "Adam"):

1. **Socket Mode** → enable. This generates an **App-Level token** (`xapp-…`) with
   scope `connections:write`. → `ADAM_SLACK_APP_TOKEN`.
2. **OAuth & Permissions** → *Bot Token Scopes*: `app_mentions:read`, `chat:write`,
   `channels:history`, `groups:history`, `im:history`, `im:read`, `im:write`.
3. **Event Subscriptions** → enable → *Subscribe to bot events*: `app_mention`,
   `message.channels`, `message.im`.
4. **Install to Workspace** → copy the **Bot User OAuth Token** (`xoxb-…`) →
   `ADAM_SLACK_BOT_TOKEN`.
5. Create the channel `#adam-dev` and **invite the Adam bot** to it (also `#crew-team`
   once Phase 2 lands).

Repeat per persona for Phase 2 (Eva/Zakarya/Sara), then flip `enabled: true` in
`crew.yaml`.

### 3. Tokens

```bash
cp .env.example .env      # then paste the two Adam tokens (xoxb-… / xapp-…)
```

## Run

```bash
./run.sh                  # activates the venv and runs `python -m crew`
```

Then in Slack, DM Adam or `@Adam` in `#adam-dev`:

> @Adam what does the TTS server do, and where's its entrypoint?

He'll work in the real Loquina repo (`/Users/m.mostafa/Workspace/code/plauda`),
branch for any code change, and reply in-thread. Watch `.logs/audit.jsonl` to see
every tool action.

**Stop:** post `crew-stop` in a channel, or Ctrl+C the process.

## Layout

```
crew.yaml                  # global config + per-persona overrides
personas/<name>/
  persona.yaml             # (reserved; per-persona config currently lives in crew.yaml)
  personality.md           # editable character
  expertise.md             # editable skills + operating rules
  memory/MEMORY.md         # persistent memory index + topic files
src/crew/
  config.py  persona.py  guardrails.py  audit.py  memory.py
  agent_session.py         # the only file that touches claude-agent-sdk
  router.py  slack_app.py  service.py  __main__.py
tests/                     # pytest suite (no live Slack / Claude needed)
```

## Tests

```bash
.venv/bin/python -m pytest -q
```
