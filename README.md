# The Crew 🤖

A configurable team of AI coworkers you direct through **Slack** and that collaborate
with each other in Slack — each a persona with its own character, expertise, memory,
Slack identity, and guardrails, working on **your** project.

Each persona is a [Claude Agent SDK](https://pypi.org/project/claude-agent-sdk/)
session. The runtime is one always-on Python process using Slack **Socket Mode** (no
inbound URL — works behind a corporate proxy). Adding or re-skilling a teammate, and
pointing the crew at a different project, are **configuration**, not code changes.

> **Ships with an example:** the included team — **Adam** (Senior Developer), **Eva**
> (Customer Support), **Zakarya** (Product Owner & Marketing), **Sara** (Designer) —
> comes configured against an example product (*Loquina*). Repoint it at your own
> project by editing `crew.yaml` and the persona files; rename or replace personas
> freely. Nothing about the framework is specific to that product.

## How it works

```
You ──Slack @Adam──▶ Crew (Socket Mode) ──▶ persona's Agent SDK session
                                            ├─ tools run in your configured repo
                                            │  (every call gated by guardrails + audited)
                                            └─ reply posts back to the thread as that persona
```

- **Config-driven** — `crew.yaml` sets each persona's access level, autonomy,
  guardrails, model, channels, and working directory.
- **Editable personality** — `personas/<name>/personality.md` (+ `expertise.md`)
  compose the system prompt. Edit and post `crew-reload` to re-characterize live.
- **Persistent memory** — `personas/<name>/memory/` survives restarts; read at session
  start, updated by the agent as it learns. Conversations resume across restarts too.
- **Collaboration** — agents hand off by `@mention`ing each other in `#crew-team`; a
  loop-guard bounds runaway agent↔agent chatter.
- **Working indicator** — 👀 on your message while a persona works, progress streamed
  as it goes, ✅ when done (see [Working indicator](#working-indicator)).
- **Feedback in** — a project's user feedback can reach a persona two ways: the crew
  **pulls** (polls a DB/API) or a project **pushes** via a secure webhook (see
  [Feedback feed](#feedback-feed-portable)).
- **Guardrails** — branch-only (never commit to a protected branch), `rm -rf` /
  force-push / prod-deploy blocked, every tool action logged to `.logs/audit.jsonl`.
- **Controls** — `crew-stop` / `crew-resume` (kill switch) and `crew-reload` (re-read
  persona files), posted in any channel.

## Setup

### 1. Python + dependencies

```bash
cd crew
python3.10 -m venv .venv
.venv/bin/pip install -e ".[dev]"   # behind a TLS-intercepting proxy: PIP_CERT=/path/to/ca-bundle.pem
```

The Agent SDK drives your **authenticated `claude` CLI** — no `ANTHROPIC_API_KEY`
needed as long as `claude` is logged in.

### 2. Point the crew at your project

Edit `crew.yaml`:
- `defaults.workdir` → your project's repo path (where agents read/edit code).
- `defaults.model`, access level, autonomy, and guardrails as you like.
- one entry per persona under `personas:` (name, role, channels, `enabled`).
- *(optional)* the `feedback:` block — see [Feedback feed](#feedback-feed-portable).

Each persona's character lives in `personas/<name>/personality.md` + `expertise.md` —
edit them for your product and team.

### 3. Create a Slack app per persona

Use the ready-made manifests in [`deploy/slack/`](deploy/slack/) — **api.slack.com/apps
→ Create New App → From a manifest**, paste the persona's YAML. That preconfigures
scopes, events, Socket Mode, the DM tab, and `reactions:write`. Then, per app:
- **Install App** → copy the **Bot token** (`xoxb-…`) → `<NAME>_SLACK_BOT_TOKEN`.
- **Basic Information → App-Level Tokens → Generate** (`connections:write`) → **App
  token** (`xapp-…`) → `<NAME>_SLACK_APP_TOKEN`.

The app's name must match the persona name so `@mention` handoffs resolve. Full
walkthrough and regeneration in `deploy/slack/README.md`.

### 4. Tokens + channels

```bash
cp .env.example .env      # paste each persona's xoxb-… / xapp-… pair
```
Create each persona's home channel, the shared **`#crew-team`**, and any feedback
channel; invite every bot to its channels (all collaborators must be in `#crew-team`).

## Run

```bash
./run.sh                  # activates the venv and runs `python -m crew`
```

You'll see one `⚡️ Bolt app is running!` per enabled persona. Then in Slack:

> @Adam what does the build do, and where's its entrypoint?

The persona works in your configured repo, branches for any code change, and replies
in-thread (DMs reply at the root). Watch `.logs/audit.jsonl` for every tool action.
**Stop:** post `crew-stop`, or Ctrl+C.

## Working indicator

While a persona works, it adds a 👀 reaction to your message, streams its progress as
messages, and swaps 👀 for ✅ when done. DMs reply at the root; channel replies thread
under your message.

> **Note:** Slack does **not** let bots show the native "X is typing…" indicator in
> channels or DMs (that's only available via the deprecated RTM API or Slack's separate
> AI-Assistant container). The 👀 → ✅ reaction is the closest chat-native equivalent.

The reaction needs the **`reactions:write`** scope on each app:
- Personas created from `deploy/slack/` manifests already include it.
- An app you created by hand may not — add `reactions:write` under **OAuth &
  Permissions** and reinstall, or that persona just won't show the 👀/✅ (replies still
  work). Without the scope the reaction is silently skipped.

## Collaboration

Address a teammate with an `@mention` in a channel (e.g. `@Zakarya what's the
priority?`). When an agent `@mentions` a teammate in `#crew-team`, that teammate picks
it up automatically. In channels the crew responds **only to @mentions** (so they
don't all answer every line); DMs respond to everything. A loop-guard caps
agent↔agent hops (default 8 in `crew.yaml`/`Router`) and resets whenever a human speaks.

**Add a teammate:** `cp -r personas/_template personas/<name>`, fill in the two
markdown files, add a `crew.yaml` entry, create the Slack app (regenerate its manifest
with `deploy/slack/generate.py`), and flip `enabled: true`.

## Feedback feed (portable)

A project's user-feedback can reach a triage persona two ways — use either or both:

- **Pull** — the crew polls the project's feedback store (SQLite or an HTTP/JSON API).
- **Push** — the project POSTs feedback to the crew's [secure webhook](#push-instead-of-pull-secure-webhook).

Both are **config-only and portable** (`crew.yaml → feedback` / `webhook`) and feed the
exact same triage path. Pull setup, via `crew.yaml → feedback`:

- `source.type: sqlite` — read-only against any SQLite DB; supply a `query` that
  aliases columns to the canonical names (`id, text, context, created_at, email,
  status`) and binds `:last_id` / `:limit`.
- `source.type: http` — GET a JSON endpoint (`{last_id}`/`{limit}` substituted), set
  `items_path` to the array and `fields` to map your shape onto the canonical names.
  `${ENV_VAR}` in `url`/`headers` expands from the environment, so tokens stay out of
  config.

The poller surfaces each new item to `feedback.persona` in `feedback.channel`, who
classifies it and `@mentions` the right teammate. The Crew tracks the last-seen id in
`state/feedback.json` and **never writes to the source DB**. Enable with
`feedback.enabled: true` once the triage persona is running. Add a new source kind by
writing a class with `fetch_since(last_id, limit)` and registering it in
`build_feedback_source` (`src/crew/feedback.py`). *(The shipped config wires this to
the example project; repoint `source` at yours.)*

### Push instead of pull: secure webhook

Rather than the crew polling a project, a project can **POST** feedback to the crew.
Configure `crew.yaml → webhook` (enabled, host/port, `secret_env`, default
persona/channel) and set the secret in the environment (`CREW_WEBHOOK_SECRET`). Any
project then calls:

```bash
curl -X POST http://127.0.0.1:8787/feedback \
  -H "Authorization: Bearer $CREW_WEBHOOK_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"text":"App crashes on login","email":"u@example.com"}'
```

Payload: `text` (required) plus optional `id`, `context`, `email`, and `persona` /
`channel` overrides. Auth is a shared secret (constant-time compared) via
`Authorization: Bearer …` or `X-Crew-Token`; the secret lives in the environment, not
config. It **binds to `127.0.0.1` by default** — to accept remote calls, front it with
a TLS-terminating reverse proxy and set `host` explicitly. Poller and webhook can run
together; both feed the same triage path.

## Layout

```
crew.yaml                  # global config + per-persona overrides + feedback source
deploy/slack/              # app manifests (paste-to-create) + generator
personas/<name>/
  personality.md           # editable character
  expertise.md             # editable skills + operating rules
  memory/MEMORY.md         # persistent memory index + topic files
personas/_template/        # skeleton for new personas
src/crew/
  config.py  persona.py  guardrails.py  audit.py  memory.py  state.py
  agent_session.py         # the only file that touches claude-agent-sdk
  feedback.py              # portable feedback sources + poller
  router.py  slack_app.py  service.py  __main__.py
tests/                     # pytest suite (no live Slack / Claude needed)
```

## Tests

```bash
.venv/bin/python -m pytest -q
```
