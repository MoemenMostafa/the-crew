#!/usr/bin/env bash
# Restart the local crew on whatever's currently checked out.
#
# Detached on purpose: the crew spawns each persona as a child `claude` process,
# so a restart triggered from *inside* a persona session would kill itself before
# it could relaunch. Running this under setsid (see the recommended invocation)
# detaches it from that process tree so it survives the kill and brings the crew
# back up. macOS has no `setsid`, so use nohup (which ignores the SIGHUP) + `&`:
#
#   nohup ./restart.sh >> .logs/crew.out 2>&1 < /dev/null &
#
# Logs go to .logs/crew.out. crew.yaml + persona prompts load at boot, so this is
# how new code/config takes effect.
set -euo pipefail
cd "$(dirname "$0")"

echo "[restart $(date '+%F %T')] stopping old crew…"
pkill -f "python -m crew" 2>/dev/null || true
sleep 2

echo "[restart $(date '+%F %T')] starting crew…"
exec ./run.sh
