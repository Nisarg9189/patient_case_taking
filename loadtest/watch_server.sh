#!/usr/bin/env bash
# Stage 2: one line of server health every INTERVAL seconds while a load test runs against
# the deployed app (deploy/). Counts are since this script started.
#
#   loadtest/watch_server.sh ubuntu@<server-ip> ~/.ssh/<key>.pem [interval=30]
set -euo pipefail
HOST=${1:?usage: watch_server.sh user@host key.pem [interval]}
KEY=${2:?usage: watch_server.sh user@host key.pem [interval]}
INTERVAL=${3:-30}
SINCE=$(date -u +%Y-%m-%dT%H:%M:%SZ)

while true; do
  ssh -i "$KEY" -o ConnectTimeout=10 "$HOST" "SINCE=$SINCE bash -s" <<'REMOTE' || echo "$(date +%H:%M:%S) (server not reachable)"
cd ~/case_taking/deploy
LOGS=$(sudo docker compose logs --no-log-prefix --since "$SINCE" app 2>&1)
count() { printf '%s\n' "$LOGS" | grep -c -i -E "$1" || true; }
STATS=$(sudo docker stats --no-stream --format '{{.CPUPerc}} cpu, {{.MemUsage}}' deploy-app-1 | sed 's| / [^ ]*||')
echo "$(date +%H:%M:%S)  $STATS | answers $(count 'Patient answer received')" \
  "| plan queued $(count 'Planning waited') retries $(count 'Planning attempt') fallbacks $(count 'Planning failed')" \
  "| facts failed $(count 'Fact extraction failed')" \
  "| gemini->recorded $(count 'Switching to the answer recorded') reconnects $(count 'Voice agent error')" \
  "| rate limits $(count 'too many concurrent|status code: 429|RESOURCE_EXHAUSTED|quota exceeded|rate limit') | tracebacks $(count 'Traceback')"
REMOTE
  sleep "$INTERVAL"
done
