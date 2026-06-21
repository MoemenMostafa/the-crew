# Slack app manifests

One manifest per persona so creating each Slack app is paste-and-go instead of
clicking through scopes, events, Socket Mode, and the DM tab.

## Use

1. Go to **https://api.slack.com/apps** → **Create New App** → **From a manifest**.
2. Pick the **loquina** workspace.
3. Paste the persona's YAML (e.g. `eva.yaml`) → **Next** → **Create**.
4. **Install App → Install to workspace** → copy the **Bot token** (`xoxb-…`).
5. **Basic Information → App-Level Tokens → Generate** (scope `connections:write`)
   → copy the **App token** (`xapp-…`).
6. Put both in `crew/.env` (`EVA_SLACK_BOT_TOKEN` / `EVA_SLACK_APP_TOKEN`, etc.).
7. Invite the bot to its channels (and `#crew-team`), flip `enabled: true` in
   `crew.yaml`, and restart `./run.sh`.

> Manifests configure everything *except* the two tokens — Slack only issues those
> on install / generation, so steps 4–5 are still manual (one click each).

## What's preconfigured

Bot scopes (`app_mentions:read`, `chat:write`, `files:read`, `files:write`, `*:history`, `im:*`, `reactions:write`),
bot events (`app_mention`, `message.channels`, `message.groups`, `message.im`),
**Socket Mode on** (no public URL), and the **Messages tab enabled** (so the persona
is DM-able).

## Regenerating

These files are generated from `_template.yaml` + `crew.yaml`:

```bash
.venv/bin/python deploy/slack/generate.py
```

Edit `_template.yaml` (shared settings) or `crew.yaml` (names/roles) and re-run.
