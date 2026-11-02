#!/usr/bin/env bash
# Copies apps saved in the editor into siddhi/ so they can be committed
. "$(dirname "$0")/common.sh"
need_docker
docker cp "$EDITOR_NAME:/home/siddhi_user/siddhi-tooling/wso2/tooling/deployment/workspace/." "$ROOT/siddhi/"
ls "$ROOT/siddhi"
