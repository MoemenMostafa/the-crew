# The Crew — AI Team over Slack — Design

**Date:** 2026-06-19
**Status:** Approved design → ready for implementation planning
**Repo:** `/Users/m.mostafa/Workspace/code/crew`

## 1. Purpose

A team of AI coworkers that operate a real software product (**Loquina**, a
voice-first German language-learning app at `/Users/m.mostafa/Workspace/code/plauda`)
and that you direct entirely through **Slack**. The team:

- **Adam** — Senior Developer
- **Eva** — Customer Support
- **Zakarya** — Product Owner
- **Sara** — Designer

Each has a distinct character, expertise, and Slack presence. You talk to them in
Slack; they talk to each other in Slack; and Loquina's customer-support feed flows
to Eva, who triages it and pulls the rest of the team in to fix bugs or build
features.

This is intended to be a **real working tool**, not a demo — the agents take real
actions on the real product, with guardrails.

## 2. Goals & success criteria

- You can `@mention` or DM any persona in Slack and get useful, in-character work back.
- Personas collaborate **autonomously and visibly**: handoffs happen in real Slack
  channels you can watch and interrupt at any time.
- Adam makes real code changes to Loquina (on branches → PRs), Eva triages real
  feedback, Zakarya prioritizes, Sara handles UI/design.
- The whole thing runs as one always-on service on the Mac, behind the corporate
  proxy, with no inbound URL required.
- Access/autonomy/guardrail levels are **configurable from a config file**, not
  hardcoded.

## 3. Non-goals (initial)

- No public/multi-tenant deployment; runs on one machine for one operator (you).
- No custom web UI — Slack is the entire interface.
- No replacing Loquina's existing systems; the Crew *uses* them.
- Phase 1 does not need the feedback feed or all four personas wired (see §11).

## 4. Architecture overview

One long-running **async Python service** ("Crew") on the Mac. It holds:

- **4 Slack connections in Socket Mode** (WebSocket; no inbound URL, works behind a
  TLS-intercepting proxy), one per persona — each persona is its **own Slack app /
  bot user** with its own name and avatar.
- **4 Claude Agent SDK sessions** (`claude-agent-sdk`, Python), one per persona, each
  **resumable** across restarts.

**Slack is the message bus.** There is no custom inter-agent queue: when one persona's
reply `@mentions` another persona, Slack delivers that as an `app_mention` event to the
other persona's app, which becomes that persona's next turn. This makes inter-agent
collaboration both autonomous and visible, for free.

```
You ──Slack──▶ #adam-dev / @Adam ──▶ Crew(Adam session) ──▶ tools (git/edit/bash in /plauda)
                                                          └─▶ reply posts to Slack
Eva ──@mentions @Adam in #crew-team──▶ Slack delivers app_mention──▶ Crew(Adam session) ...
Loquina feedback ──adapter──▶ #loquina-feedback ──▶ Crew(Eva session) triages ──▶ @mentions
```

## 5. Slack identity & channels

- **4 separate Slack apps / bot users**, one per persona — so each can be `@mentioned`,
  DMed, and invited to channels like a real coworker, and `@mention` routing "just
  works." One Crew process runs all four socket connections (each app provides a bot
  token + an app-level token for Socket Mode).
- **Channels:**
  - Home channels: `#adam-dev`, `#eva-support`, `#zakarya-product`, `#sara-design`
  - `#crew-team` — shared channel for cross-functional collaboration, visible to you
  - `#loquina-feedback` — support feed intake; Eva watches it
- Runs inside an **existing Slack workspace** you already use, so channel membership is
  scoped deliberately (agents are only in the channels above).

## 6. Turn lifecycle

1. Slack event arrives on a persona's socket (DM, `app_mention`, or thread reply in a
   channel the persona is in).
2. Crew assembles the turn: message text + thread context + sender identity (human vs.
   another bot) + channel.
3. Crew feeds the turn to that persona's Agent SDK session (resuming it).
4. The SDK streams the work; tool calls execute (edits, git, feedback reads). For long
   jobs, a "🛠️ working…" ack posts immediately and optional progress streams to the
   thread.
5. The agent's final reply posts back to the originating channel/thread under that
   persona's bot identity.
6. If the reply `@mentions` another persona → step 1 for that persona. (Loop-guard: see
   §10.)

## 7. Persona definition (config-driven, easily editable)

Each persona lives in `crew/personas/<name>/` and is **fully defined by plain editable
files** — no code changes to add, retune, or re-character a persona:

- `persona.yaml` — display name, role, bot-token + app-token env var names, channels,
  model, working directory, allowed tools, MCP servers, and **per-persona overrides**
  of the global access/autonomy settings (see §8).
- `personality.md` — **the character**: voice, tone, quirks, communication style. Kept
  separate from expertise precisely so personality is **easy to tweak** in isolation.
- `expertise.md` — domain skills, responsibilities, operating rules, and guardrails (the
  "what they're good at and how they work").
- `memory/` — persistent long-term memory (see §7a).

`personality.md` + `expertise.md` are composed into the persona's system prompt at
session start. Editing either and reloading the persona (or `/crew-reload <name>` in
Slack) re-characterizes that agent immediately — **no restart of the whole service, no
code edit**.

Working directories:
- **Adam** → the real Loquina repo (`/Users/m.mostafa/Workspace/code/plauda`).
- **Eva / Zakarya / Sara** → their own workspace under `crew/personas/<name>/workspace/`,
  with read access to the Loquina repo as needed.

### 7a. Memory (first-class)

Each persona has **persistent memory** under `crew/personas/<name>/memory/` that survives
restarts and is independent of the LLM context window:

- A `memory/MEMORY.md` index plus topic files (decisions made, ongoing work, your stated
  preferences, recurring facts about Loquina).
- **Read on session start** (injected into context) and **updated by the agent itself**
  via a memory tool as work progresses — the same persisted-fact pattern, scoped per
  persona so Adam's engineering memory and Eva's support memory stay separate.
- Conversation continuity also comes from the **resumable Agent SDK session id** (§12);
  `memory/` is the durable, human-readable layer that outlives any single session and can
  be edited or audited by you directly.

## 8. Configuration (`crew.yaml`)

A single global config file at the repo root drives **access, autonomy, and
guardrails**, with per-persona overrides in each `persona.yaml`. Nothing about access
level is hardcoded.

```yaml
# crew.yaml (illustrative)
defaults:
  access_level: full          # full | propose | sandboxed
  autonomy: autonomous         # autonomous | approve_handoffs | direct_only
  external_comms: gated        # gated | autonomous | readonly
  protected_branches: [main, master]
  block_destructive: true      # rm -rf, force-push to protected, prod deploy
  audit_log: .logs/audit.jsonl

personas:
  adam:
    access_level: full
    workdir: /Users/m.mostafa/Workspace/code/plauda
    require_branch: true       # never commit directly to a protected branch
  eva:
    external_comms: gated      # drafts replies; sending to a real user needs 👍
  zakarya: {}
  sara: {}
```

- `access_level` — `full` (act directly), `propose` (read + draft only, human ships),
  `sandboxed` (work only in an isolated worktree/copy).
- `autonomy` — how inter-agent handoffs flow (`autonomous` = visible, no gate;
  `approve_handoffs` = wait for 👍; `direct_only` = no agent↔agent).
- `external_comms` — whether sending messages to real Loquina users is `gated`,
  `autonomous`, or moot (`readonly` feed).

Changing a level is a config edit + service reload — no code change.

## 9. Concurrency & long tasks

- Each persona processes its turns on its **own async queue**, so a 10-minute Adam build
  never blocks Eva.
- A persona **serializes its own turns** (one Adam task at a time) to avoid working-tree
  clobbering; the four personas run in parallel.
- Long runs execute in the background; the result is posted to the thread when done.

## 10. Guardrails (full hands-on)

Chosen access level is `full`, so guardrails matter:

- **Branch discipline:** Adam never commits to a protected branch (`require_branch`);
  work goes on a feature branch/worktree → PR.
- **Destructive-op block:** `rm -rf`, force-push to protected branches, and prod deploys
  are blocked via Agent SDK **permission hooks** (a `can_use_tool` callback inspecting
  the command), independent of the system prompt.
- **External-comms gate:** Eva drafts user-facing replies in Slack; actually sending to a
  real customer requires your 👍. Internal actions (filing bugs, handoffs) stay autonomous.
- **Audit log:** every tool action → `crew/.logs/audit.jsonl` (persona, tool, args
  summary, timestamp, channel).
- **Kill switch:** a `/crew-stop` Slack command (and Ctrl+C) pauses all agents; `/crew-start`
  resumes.
- **Loop-guard:** a bounded depth / rate limit on agent→agent mention chains so two
  personas can't ping-pong indefinitely; on hitting the bound, the chain pauses and
  notifies you.

## 11. Eva ↔ Loquina feedback

A small **adapter** (a custom MCP server or tool) reads Loquina's existing feedback feed
(exact endpoints from the Loquina ops-console/analytics API to be wired during
implementation). New items post into `#loquina-feedback`. Eva:

1. Triages each item (bug / feature request / praise / confusion).
2. Drafts a user-facing reply (held for your 👍 per the external-comms gate).
3. When action is needed, `@mentions` Adam (fix) or Zakarya (prioritize) in `#crew-team`.

## 12. State & persistence

- `crew/state/sessions.json` — persona → Agent SDK session id (for resume across restarts).
- `crew/personas/<name>/memory/` — long-term notes maintained by each agent.
- `crew/.logs/audit.jsonl` + per-turn transcript logs.

## 13. Tech stack

- Python 3.10+ with `uv` (matches Loquina's toolchain).
- `claude-agent-sdk` — the persona engine (programmatic Claude Code: tools, MCP, skills,
  hooks, permissions, resumable sessions).
- `slack-bolt` in **Socket Mode** — Slack connectivity, no inbound URL.
- New repo at `/Users/m.mostafa/Workspace/code/crew`, own venv, `.env` for the 4×2 Slack
  tokens + Anthropic key. The corporate `ca-bundle.pem` pattern from Loquina is reused if
  the proxy interferes.

## 14. Build order (phased — each phase independently useful)

The spec captures the full vision; implementation starts at Phase 1.

- **Phase 1 — Adam, end to end.** The spine: Crew service, one Socket Mode connection,
  one Agent SDK session in the Loquina repo, full message round-trip, `crew.yaml`/persona
  config, branch + destructive-op guardrails, audit log. Proves the entire architecture.
- **Phase 2 — Full team + `@mention` handoffs.** Add Eva, Zakarya, Sara; `#crew-team`;
  native inter-agent mention routing; per-persona concurrency; loop-guard; kill switch.
- **Phase 3 — Eva's feedback feed.** Loquina feedback ingestion → triage → handoffs;
  external-comms gate.
- **Phase 4 — Always-on hardening.** `launchd` daemon to keep it running, richer
  guardrails/dashboards, recovery on crash.

## 15. Key risks & mitigations

- **Real product mutations under full access** → branch discipline, destructive-op
  hooks, audit log, kill switch, loop-guard (§10).
- **Agent↔agent infinite loops** → bounded mention-chain depth + rate limit (§10).
- **Working-tree conflicts from concurrent Adam tasks** → per-persona serialization +
  branches/worktrees (§9, §10).
- **Existing-workspace blast radius** → agents confined to the named channels (§5).
- **Proxy/TLS interference** → reuse Loquina's `ca-bundle.pem` wiring (§13).
