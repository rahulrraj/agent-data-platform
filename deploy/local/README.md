# Run the stack locally in containers

This is the container path of the local run guide — the other two ways
(no containers, Kubernetes/kind) are in [`doc/run-locally.md`](../doc/run-locally.md).

Three containers on one Docker network: the API, the UI (nginx serving
the production build), and an nginx front door that routes `/api/*` to
the API and everything else to the UI — the local equivalent of the k8s
Ingress (`deploy/k8s/ingress.yaml`). This is the documented form of the
setup Rahul ran on 2026-11-24: login + chat worked end to end through
it, which is the first live verification of the Okta login flow (see
`TODO.md`, "Live Okta verification").

## Contents

- [Prerequisites](#prerequisites)
- [Run it](#run-it)
- [Settings](#settings)
- [Notes](#notes)
- [What this does and does not prove](#what-this-does-and-does-not-prove)

## Prerequisites

- Docker, aws CLI v2, bash.
- An AWS SSO profile with Bedrock access (`us.anthropic.claude-sonnet-5-5`
  and `amazon.titan-embed-text-v2:0` in `us-east-1`).
- An Okta SPA app (Okta first, generic OIDC path — ADR 0008) with the
  front-door address registered as a sign-in redirect URI **and** a
  Trusted Origin (CORS). Default address: `http://localhost:8501`.
- The repo checked out (the script defaults `REPO_DIR` to the repo that
  contains it).

## Run it

```bash
export AWS_PROFILE=<your SSO profile>
export AUTH_ISSUER=https://<tenant>.oktapreview.com/oauth2/<auth-server-id>
export AUTH_AUDIENCE=api://<your api audience>
export VITE_OIDC_ISSUER="$AUTH_ISSUER"
export VITE_OIDC_CLIENT_ID=<the Okta SPA app's client id>

deploy/local/run.sh build   # base images + API image + UI image
deploy/local/run.sh up      # network + 3 containers (fresh AWS creds)
deploy/local/run.sh down    # remove the containers
```

Then open `http://localhost:8501` and sign in.

## Settings

| Variable | Default | What it is |
| --- | --- | --- |
| `AWS_PROFILE` | — (required for `up`) | SSO profile; credentials are exported fresh on every `up` |
| `AWS_REGION` | `us-east-1` | Bedrock region |
| `AGENT_MODEL_PROVIDER` | `bedrock` | `ollama` / `gemini` / `bedrock` |
| `AUTH_ISSUER` | — (required) | OIDC issuer for the API (fail-closed: the API refuses to start without it) |
| `AUTH_AUDIENCE` | — (required) | Expected `aud` claim |
| `VITE_OIDC_ISSUER` | — (required for `build`) | Same issuer, for the UI bundle (build-time only) |
| `VITE_OIDC_CLIENT_ID` | — (required for `build`) | Okta SPA app's client id (build-time only) |
| `CAPABILITIES_PATH` | `registry/capabilities.yaml` | Registry file inside the API image |
| `DATA_CONNECTIONS_FILE` | — | File whose contents become `DATA_CONNECTIONS` (service credentials — never commit it) |
| `DATA_CONNECTIONS` | — | Same, as an env var directly |
| `REPO_DIR` | the repo containing the script | Where the Dockerfiles live |
| `NETWORK` | `agent-net` | Docker network for the three containers |
| `API_IMAGE` / `UI_IMAGE` | `agent-api:local` / `agent-ui:local` | Image tags |
| `API_PORT` | `8000` | Host port for the API (also reachable via the front door) |
| `FRONT_DOOR_PORT` | `8501` | Host port for the front door — the Okta-registered address |
| `FRONT_DOOR_CONF` | `deploy/local/front-door.conf` | nginx config for the front door |

## Notes

- **Docker Hub rate limits.** The script pulls the base images from ECR
  Public (`public.ecr.aws/...`) and tags them with the names the
  Dockerfiles use, because Docker Hub returns 429 on rate-limited
  networks. If you change a `FROM` line, change the matching pull here.
- **AWS credentials expire.** They are short-lived keys exported from
  your SSO profile. If Bedrock calls start failing, run `up` again for
  fresh ones. A deployed container would use an IAM role instead (the
  Lambda stack already wires one — see `deploy/terraform/README.md`).
- **A UI settings change needs a rebuild** (`run.sh build`): the OIDC
  values are baked into the bundle at build time. The Dockerfile takes
  no build arguments, so the build writes a temporary
  `src/ui/.env.production` and deletes it afterwards — do not commit
  that file.
- **Capability changes need a rebuild too**: the Dockerfile copies
  `registry/` into the API image. Or mount a file and point
  `CAPABILITIES_PATH` at it, e.g. add
  `-v /path/to/registry.yaml:/app/registry/custom.yaml:ro` to the API
  `docker run` and set `CAPABILITIES_PATH=registry/custom.yaml`.
- **The front door logs no query strings** (`front-door.conf`): nginx's
  default access log would record the Okta callback URL with
  `?code=...&state=...`. The custom format logs the path only.
- **The UI does not need its container.** The UI is static files; the
  container is one way to serve them. Alternatives that give the same
  login and chat: `npm run dev` (Vite proxies `/api/*` to
  `localhost:8000`), any static host (S3 + CloudFront via the Terraform
  `frontend` module), or serving `dist/` from the front door itself.
  Whatever serves it, the origin must be registered in Okta and the
  bundle built with that origin's `VITE_OIDC_*` values.

## What this does and does not prove

Proved by the 2026-11-24 run: live login redirect + callback, real
discovery document and JWKS-backed tokens through the composed app, the
registered redirect URI working end to end, Bedrock chat + capability
tools from inside a container, and the front-door routing pattern.

Still unverified: refresh-token issuance/rotation and silent renewal
through a real tenant, and the Function URL `NONE` + CloudFront + WAF
front-door interplay (this stack uses a local nginx front door, not
CloudFront). See `TODO.md`.
