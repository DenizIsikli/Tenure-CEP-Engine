#!/usr/bin/env bash
# Runs every app in siddhi/ without the editor (for the demo)
. "$(dirname "$0")/common.sh"
need_docker
docker rm -f "$EDITOR_NAME" "$RUNNER_NAME" >/dev/null 2>&1 || true   # both use port 8006
docker run -d --name "$RUNNER_NAME" \
  -p 8006:8006 \
  --add-host=host.docker.internal:host-gateway \
  -v "$ROOT/siddhi":/apps \
  "$RUNNER_IMAGE" -Dapps=/apps
echo "Runner started with apps from siddhi/   (logs: scripts/logs.sh runner)"
