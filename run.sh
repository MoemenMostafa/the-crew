#!/usr/bin/env bash
# Launch the Crew. Requires .env (copy from .env.example and fill in Slack tokens)
# and an authenticated `claude` CLI (the Agent SDK uses its credentials).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "No .venv found. Create it with: python3.10 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 1
fi
if [ ! -f .env ]; then
  echo "No .env found. Copy .env.example to .env and fill in your Slack tokens." >&2
  exit 1
fi

# Reuse Loquina's CA bundle if present (corporate TLS-intercepting proxy).
CA="/Users/m.mostafa/Workspace/code/plauda/ca-bundle.pem"
if [ -f "$CA" ]; then
  export REQUESTS_CA_BUNDLE="$CA"
  export SSL_CERT_FILE="$CA"
  export NODE_EXTRA_CA_CERTS="$CA"
fi

exec .venv/bin/python -m crew
