#!/usr/bin/env bash
# Local three-container stack: API + UI + nginx front door.
#
# Reconstructed 2026-11-25 from Rahul's working run (2026-11-24) on his
# laptop — see deploy/local/README.md. His values (Okta tenant, AWS SSO
# profile, registry file) are NOT in here: everything deployment-specific
# comes from environment variables, listed in the README. Nothing in this
# script is a secret; the secrets arrive at runtime from your SSO
# profile and your Okta app.
#
# Usage, from anywhere:
#   deploy/local/run.sh build   # base images + API image + UI image
#   deploy/local/run.sh up      # network + 3 containers (fresh AWS creds)
#   deploy/local/run.sh down    # remove the containers
#
# Requires: docker, aws CLI v2 (with an SSO profile), bash.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"

NETWORK="${NETWORK:-agent-net}"
API_IMAGE="${API_IMAGE:-agent-api:local}"
UI_IMAGE="${UI_IMAGE:-agent-ui:local}"
API_PORT="${API_PORT:-8000}"
FRONT_DOOR_PORT="${FRONT_DOOR_PORT:-8501}"
FRONT_DOOR_CONF="${FRONT_DOOR_CONF:-$SCRIPT_DIR/front-door.conf}"

# Model + region. Bedrock is the default: the API container has no AWS
# keys of its own — it gets short-lived SSO credentials at `up` time.
AWS_REGION="${AWS_REGION:-us-east-1}"
AGENT_MODEL_PROVIDER="${AGENT_MODEL_PROVIDER:-bedrock}"

# Auth (fail-closed: the API refuses to start without these).
# AUTH_ISSUER / AUTH_AUDIENCE are the API's settings; VITE_OIDC_ISSUER /
# VITE_OIDC_CLIENT_ID are the UI's build-time settings — same tenant,
# different variables, both required.
: "${AUTH_ISSUER:?set AUTH_ISSUER (e.g. https://<tenant>.oktapreview.com/oauth2/<auth-server-id>)}"
: "${AUTH_AUDIENCE:?set AUTH_AUDIENCE (e.g. api://sandbox.example.com)}"

CAPABILITIES_PATH="${CAPABILITIES_PATH:-registry/capabilities.yaml}"
# DATA_CONNECTIONS: either a file (DATA_CONNECTIONS_FILE) whose contents
# become the env var, or the env var itself. The JSON holds service
# credentials — never commit the file.
DATA_CONNECTIONS_ARG=()
if [[ -n "${DATA_CONNECTIONS_FILE:-}" ]]; then
    DATA_CONNECTIONS_ARG=(-e "DATA_CONNECTIONS=$(cat "$DATA_CONNECTIONS_FILE")")
elif [[ -n "${DATA_CONNECTIONS:-}" ]]; then
    DATA_CONNECTIONS_ARG=(-e "DATA_CONNECTIONS=$DATA_CONNECTIONS")
fi

# Docker Hub returns 429 (rate limit) on some networks. The same official
# images are on ECR Public; pull from there and tag with the names the
# Dockerfiles use, so the builds below need no change.
pull_base_images() {
    docker pull public.ecr.aws/docker/library/python:3.12-slim
    docker tag public.ecr.aws/docker/library/python:3.12-slim python:3.12-slim
    docker pull public.ecr.aws/docker/library/node:20-slim
    docker tag public.ecr.aws/docker/library/node:20-slim node:20-slim
    docker pull public.ecr.aws/nginx/nginx-unprivileged:1.28-alpine
    docker tag public.ecr.aws/nginx/nginx-unprivileged:1.28-alpine \
        nginxinc/nginx-unprivileged:1.28-alpine
}

build_api() {
    # The repo's Dockerfile as is: copies src/ and registry/, including
    # the capability files. Changing capabilities means rebuilding, or
    # mounting a file and pointing CAPABILITIES_PATH at it (see README).
    docker build -t "$API_IMAGE" "$REPO_DIR"
}

build_ui() {
    # The UI's Okta settings are build-time only and the Dockerfile has
    # no build arguments, so a temporary .env.production is written for
    # the build and deleted afterwards (trap: also on failure).
    : "${VITE_OIDC_ISSUER:?set VITE_OIDC_ISSUER (same issuer as AUTH_ISSUER)}"
    : "${VITE_OIDC_CLIENT_ID:?set VITE_OIDC_CLIENT_ID (the Okta SPA app client id)}"
    local env_file="$REPO_DIR/src/ui/.env.production"
    trap 'rm -f "$env_file"' EXIT
    printf 'VITE_OIDC_ISSUER=%s\nVITE_OIDC_CLIENT_ID=%s\n' \
        "$VITE_OIDC_ISSUER" "$VITE_OIDC_CLIENT_ID" > "$env_file"
    docker build -t "$UI_IMAGE" -f "$REPO_DIR/src/ui/Dockerfile" "$REPO_DIR"
    rm -f "$env_file"
    trap - EXIT
}

cmd_build() {
    pull_base_images
    build_api
    build_ui
}

cmd_up() {
    : "${AWS_PROFILE:?set AWS_PROFILE (an SSO profile; its credentials are exported fresh on every up)}"
    docker network create "$NETWORK" 2>/dev/null || true

    # API: settings go in as environment variables. AWS credentials are
    # short-lived keys exported from the SSO profile — they expire, so
    # if Bedrock calls start failing, run `up` again for fresh ones.
    # A deployed container would use an IAM role instead (see README).
    docker run -d --name agent-api --network "$NETWORK" -p "$API_PORT:8000" \
        --env-file <(aws configure export-credentials \
            --profile "$AWS_PROFILE" --format env-no-export) \
        -e "AWS_REGION=$AWS_REGION" \
        -e "AGENT_MODEL_PROVIDER=$AGENT_MODEL_PROVIDER" \
        -e "AUTH_ISSUER=$AUTH_ISSUER" \
        -e "AUTH_AUDIENCE=$AUTH_AUDIENCE" \
        -e "CAPABILITIES_PATH=$CAPABILITIES_PATH" \
        "${DATA_CONNECTIONS_ARG[@]}" \
        "$API_IMAGE"

    # UI: nginx serving the production build on port 8080, reachable
    # only on the Docker network — the front door is the only way in.
    docker run -d --name agent-ui --network "$NETWORK" "$UI_IMAGE"

    # Front door: nginx on host port 8501, the address registered in
    # Okta. Sends /api/* to the API container and everything else to
    # the UI container (see front-door.conf).
    docker run -d --name agent-door --network "$NETWORK" -p "$FRONT_DOOR_PORT:8080" \
        -v "$FRONT_DOOR_CONF:/etc/nginx/conf.d/default.conf:ro" \
        nginxinc/nginx-unprivileged:1.28-alpine

    echo "UI: http://localhost:$FRONT_DOOR_PORT"
    echo "API (via the front door): http://localhost:$FRONT_DOOR_PORT/api/health"
}

cmd_down() {
    docker rm -f agent-api agent-ui agent-door 2>/dev/null || true
    docker network rm "$NETWORK" 2>/dev/null || true
}

case "${1:-}" in
    build) cmd_build ;;
    up)    cmd_up ;;
    down)  cmd_down ;;
    *)
        echo "usage: $0 {build|up|down}" >&2
        echo "  build  # base images + API image + UI image" >&2
        echo "  up     # network + 3 containers (fresh AWS credentials each time)" >&2
        echo "  down   # remove the containers" >&2
        exit 2
        ;;
esac
