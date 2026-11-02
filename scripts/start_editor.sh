#!/usr/bin/env bash
# Starts the Siddhi editor at http://localhost:9390/editor
. "$(dirname "$0")/common.sh"
need_docker
docker rm -f "$EDITOR_NAME" >/dev/null 2>&1 || true
docker run -d --name "$EDITOR_NAME" \
  -p 9390:9390 -p 9743:9743 -p 8006:8006 \
  --add-host=host.docker.internal:host-gateway \
  "$EDITOR_IMAGE"
echo "Editor starting: http://localhost:9390/editor   (logs: scripts/logs.sh editor)"
