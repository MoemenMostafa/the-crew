# The Crew 🤖

A team of AI coworkers — **Adam** (Senior Developer), **Eva** (Customer Support),
**Zakarya** (Product Owner), **Sara** (Designer) — that you direct through Slack and
that collaborate with each other in Slack. They operate the **Loquina** product.

Each persona is a [Claude Agent SDK](https://pypi.org/project/claude-agent-sdk/)
session with its own character, expertise, memory, Slack identity, and guardrails.
The runtime is one always-on Python process using Slack **Socket Mode** (no inbound
URL — works behind a corporate proxy).

> **Status:** Adam (Senior Dev) is live. Eva (Support), Zakarya (Product), and Sara
> (Design) are written and ready — they just need their own Slack apps and an
> `enabled: true` flip. Inter-agent `@mention` handoffs and the loop-guard are wired.
> See `docs/superpowers/specs/` for the design and `docs/superpowers/plans/` for the plan.

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

## Adding the rest of the team (Eva, Zakarya, Sara)

Their personas are already written (`personas/<name>/`) and stubbed in `crew.yaml`.
To bring each one online:

1. **Create a Slack app per persona** — same steps as Adam (§B–E above), naming the
   app exactly **Eva** / **Zakarya** / **Sara**. The name matters: handoffs rely on an
   agent typing `@Eva` resolving to that bot, so the app's handle must match.
2. **Tokens** → add each pair to `.env` (`EVA_SLACK_BOT_TOKEN` / `EVA_SLACK_APP_TOKEN`,
   etc. — placeholders already in `.env.example`).
3. **Channels** — create `#eva-support`, `#zakarya-product`, `#sara-design`, the shared
   **`#crew-team`**, and `#loquina-feedback`; invite each bot to its channels (every bot
   that should collaborate must be in `#crew-team`).
4. **Enable** them in `crew.yaml`: flip `enabled: true` for eva/zakarya/sara.
5. Restart `./run.sh` — you'll see one `⚡️ Bolt app is running!` per persona.

**How they collaborate:** address someone with an `@mention` in a channel (e.g.
`@Zakarya what should we build first?`). When an agent `@mentions` a teammate in
`#crew-team`, that teammate picks it up automatically. A loop-guard caps back-and-forth
agent chatter (default 8 hops) and resets whenever a human speaks. In channels the crew
responds **only to @mentions** (so they don't all answer every line); DMs respond to
everything.

**To add a future teammate:** `cp -r personas/_template personas/<name>`, fill in the two
markdown files, add a `crew.yaml` entry, create the Slack app, flip `enabled: true`.

**Optional — working indicator:** add the **`reactions:write`** scope to each app (OAuth &
Permissions → reinstall) to get the 👀-while-working → ✅-when-done reaction. Without it,
replies still work; the reaction is just skipped.

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
