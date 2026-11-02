#!/usr/bin/env bash
# Shared settings for all scripts
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EDITOR_IMAGE="siddhiio/siddhi-tooling"
RUNNER_IMAGE="siddhiio/siddhi-runner-alpine"
EDITOR_NAME="siddhi-editor"
RUNNER_NAME="siddhi-runner"
[ -f "$ROOT/.env" ] && set -a && . "$ROOT/.env" && set +a

need_docker() {
  command -v docker >/dev/null || { echo "Docker is not installed. Run scripts/setup.sh"; exit 1; }
  docker info >/dev/null 2>&1 || { echo "Docker is not running or you lack permission. Run scripts/setup.sh"; exit 1; }
}
