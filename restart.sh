#!/usr/bin/env bash
# Restart the local crew on whatever's currently checked out.
#
# Detached on purpose: the crew spawns each persona as a child `claude` process,
# so a restart triggered from inside a persona session would kill itself before it
# could relaunch. macOS has no `setsid`, so run it under nohup (ignores SIGHUP):
#
#   nohup ./restart.sh >> .logs/crew.out 2>&1 < /dev/null &
#
# NOTE: pgrep/pkill -f don't see the supervisor in this environment (return
# nothing), so we find + kill it via `ps` instead. crew.yaml + persona prompts +
# code load at boot, so this is how new code/config takes effect.
set -euo pipefail
cd "$(dirname "$0")"

echo "[restart $(date '+%F %T')] stopping old crew…"
for pid in $(ps ax -o pid=,command= | grep "[p]ython -m crew" | awk '{print $1}'); do
  echo "  killing supervisor $pid"
  kill "$pid" 2>/dev/null || true
done
sleep 3  # let the port (8787 webhook) + Slack sockets free up

echo "[restart $(date '+%F %T')] starting crew…"
exec ./run.sh
