#!/usr/bin/env bash
# Installs Docker if missing, starts it, and pulls the Siddhi images
. "$(dirname "$0")/common.sh"

if ! command -v docker >/dev/null; then
  echo "Installing Docker (docker.io)..."
  sudo apt-get update
  sudo apt-get install -y docker.io
fi

if ! systemctl is-active --quiet docker; then
  echo "Starting Docker..."
  sudo systemctl enable --now docker
fi

if ! id -nG "$USER" | grep -qw docker; then
  sudo usermod -aG docker "$USER"
  echo "Added $USER to the docker group. Log out and in (or run 'newgrp docker'), then run setup.sh again."
  exit 0
fi

command -v python3 >/dev/null || sudo apt-get install -y python3

docker pull "$EDITOR_IMAGE"
docker pull "$RUNNER_IMAGE"
[ -f "$ROOT/.env" ] || cp "$ROOT/.env.example" "$ROOT/.env"
echo "Setup done."
