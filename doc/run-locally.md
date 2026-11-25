# Run it locally — three ways

The same app, three runtimes. Pick by what you are doing; the login
and chat are the same in all three, and the model/provider settings
are the same environment variables in each.

## Contents

- [Which way](#which-way)
- [Shared prerequisites](#shared-prerequisites)
- [Way 1: No containers](#way-1-no-containers)
- [Way 2: Containers](#way-2-containers)
- [Way 3: Kubernetes (kind)](#way-3-kubernetes-kind)
- [Troubleshooting](#troubleshooting)

## Which way

| Way | Use it for | Origin (register in Okta) |
| --- | --- | --- |
| 1. No containers | Iterating on code — fastest reload, debugger attached | `http://localhost:5173` |
| 2. Containers | A faithful prod-like stack on one machine; the setup already verified end to end (2026-11-24) | `http://localhost:8501` |
| 3. Kubernetes (kind) | Testing the real manifests + Ingress before any cloud | `https://agent-platform.example.com` |

The UI is static files in every way: the only thing that changes is
what serves them. Whatever serves them, two invariants hold: the
bundle is built with that deployment's `VITE_OIDC_*` values baked in
(changing them needs a rebuild), and the origin must be registered in
Okta as a sign-in redirect URI **and** a Trusted Origin (CORS).

## Shared prerequisites

- The repo checked out.
- An Okta SPA app (Okta first, generic OIDC path — ADR 0008). Register
  **each** origin you use from the table above.
- A model provider: Ollama (local models), Gemini (API key), or
  Bedrock (AWS credentials). The guide shows Bedrock where a provider
  has to be picked; swap `AGENT_MODEL_PROVIDER` + its credentials for
  the others (see the main README's environment table).

## Way 1: No containers

Two terminals, from the repo root.

```bash
# terminal 1 — the API on :8000
pip install -e ".[dev]"        # once; or use the venv that has the deps
export AUTH_ISSUER=https://<tenant>.oktapreview.com/oauth2/<auth-server-id>
export AUTH_AUDIENCE=api://<your api audience>
export AGENT_MODEL_PROVIDER=bedrock
export AWS_REGION=us-east-1
# AWS credentials: whatever your shell already has (SSO profile,
# env keys, or `aws sso login` first)
python -m agent_api.main
```

```bash
# terminal 2 — the UI on :5173
cd src/ui
npm ci                          # once (node_modules is not committed —
                                # a fresh checkout runs this first)
VITE_OIDC_ISSUER=https://<tenant>.oktapreview.com/oauth2/<auth-server-id> \
VITE_OIDC_CLIENT_ID=<the Okta SPA app's client id> \
npm run dev
```

Open `http://localhost:5173` and sign in. Vite proxies `/api/*` to
`localhost:8000`, so no CORS setup is needed, and the redirect URI is
`window.location.origin` — `http://localhost:5173`.

**No-auth dev shortcut** (never for a shared deployment): skip the
Okta values and set `AUTH_DISABLED=true` for the API and
`VITE_AUTH_DISABLED=true` for the UI. The UI flag is honored only in
dev builds; production builds ignore it. Never set `AUTH_DISABLED`
together with `AUTH_ISSUER` — pick one.

**Providers:** Ollama is the default (`AGENT_MODEL_PROVIDER` unset):
it needs Ollama running with `qwen2.5:1.5b` and `nomic-embed-text`
installed. Gemini needs `GEMINI_API_KEY`. Bedrock needs AWS
credentials with model access in `us-east-1`.

## Way 2: Containers

Three containers on one Docker network — API, UI (nginx), and an
nginx front door on host port 8501. Full detail, settings table, and
notes live in [`deploy/local/README.md`](../deploy/local/README.md);
the short version:

```bash
export AWS_PROFILE=<your SSO profile>
export AUTH_ISSUER=https://<tenant>.oktapreview.com/oauth2/<auth-server-id>
export AUTH_AUDIENCE=api://<your api audience>
export VITE_OIDC_ISSUER="$AUTH_ISSUER"
export VITE_OIDC_CLIENT_ID=<the Okta SPA app's client id>

deploy/local/run.sh build   # base images (ECR Public) + API + UI images
deploy/local/run.sh up      # network + 3 containers, fresh AWS creds
deploy/local/run.sh down    # remove the containers
```

Open `http://localhost:8501`. This is the setup Rahul ran on
2026-11-24: login + chat worked end to end, which is the first live
verification of the Okta login flow. Notes that bite: AWS credentials
are short-lived (re-run `up` when Bedrock calls start failing), a UI
settings change needs a rebuild, and capability changes need a rebuild
or a mounted registry file — all in the local README.

## Way 3: Kubernetes (kind)

A local kind cluster running the real manifests from `deploy/k8s/`.
Needs: docker, `kind`, `kubectl`, `openssl`. Nothing here is
deployed to a cloud.

```bash
# 1. Cluster + ingress controller
kind create cluster --name agent-platform --config deploy/local/kind-config.yaml
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
kubectl -n ingress-nginx wait --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller --timeout=180s

# 2. Images, tagged exactly as the manifests expect
export AUTH_ISSUER=https://<tenant>.oktapreview.com/oauth2/<auth-server-id>
export AUTH_AUDIENCE=api://<your api audience>
export VITE_OIDC_ISSUER="$AUTH_ISSUER"
export VITE_OIDC_CLIENT_ID=<the Okta SPA app's client id>
API_IMAGE=agent-data-platform:latest UI_IMAGE=agent-ui:latest \
  deploy/local/run.sh build
kind load docker-image agent-data-platform:latest agent-ui:latest \
  --name agent-platform

# 3. Tenant + secrets. Edit deploy/k8s/configmap.yaml: set AUTH_ISSUER /
#    AUTH_AUDIENCE to your tenant (placeholders are example.okta.com),
#    and AGENT_MODEL_PROVIDER to "bedrock" (Sonnet; the default ollama
#    has no server in this cluster, and the API pods stay not-ready
#    without a model backend — readiness pings the model and the
#    embeddings). AWS_REGION is already us-east-1 there.
#    The namespace first: the Secret needs it to exist (step 5 applies
#    it again, harmlessly).
kubectl apply -f deploy/k8s/namespace.yaml
#    Then the Secret — the in-cluster Postgres user/password are both
#    `agent` (see postgres-statefulset.yaml; dev only, never prod).
#    Bedrock needs short-lived keys from your SSO profile (same source
#    as the container run); they expire, so re-run this command and
#    `kubectl -n agent-platform rollout restart deployment/agent-api`
#    when Bedrock calls start failing. Never commit these values.
eval "$(aws configure export-credentials --profile <your-sso-profile> --format env-no-export)"
kubectl -n agent-platform create secret generic agent-platform-secrets \
  --from-literal=POSTGRES_DSN='postgres://agent:agent@postgres:5432/agentdb' \
  --from-literal=DATA_CONNECTIONS='{}' \
  --from-literal=GEMINI_API_KEY='' \
  --from-literal=AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
  --from-literal=AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
  --from-literal=AWS_SESSION_TOKEN="$AWS_SESSION_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -

# 4. TLS (self-signed — Okta accepts only https redirect URIs for
#    non-localhost hosts, so the Ingress must serve https).
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout tls.key -out tls.crt -subj "/CN=agent-platform.example.com" \
  -addext "subjectAltName=DNS:agent-platform.example.com"
kubectl -n agent-platform create secret tls agent-platform-tls \
  --cert=tls.crt --key=tls.key
#    Uncomment the tls: block in deploy/k8s/ingress.yaml (it references
#    secretName agent-platform-tls).

# 5. Apply — file by file, NOT `kubectl apply -k`: the kustomization
#    includes secret.yaml, which is an untracked local-only template
#    (absent in a fresh clone, and the committed copy must never be
#    applied with its placeholder password).
kubectl apply -f deploy/k8s/namespace.yaml
kubectl apply -f deploy/k8s/configmap.yaml
kubectl apply -f deploy/k8s/postgres-statefulset.yaml
kubectl apply -f deploy/k8s/serviceaccount.yaml
kubectl apply -f deploy/k8s/api-deployment.yaml
kubectl apply -f deploy/k8s/ui-deployment.yaml
kubectl apply -f deploy/k8s/ingress.yaml

# 6. Host entry + Okta registration
echo "127.0.0.1 agent-platform.example.com" | sudo tee -a /etc/hosts
#    In Okta, register https://agent-platform.example.com as a sign-in
#    redirect URI and Trusted Origin for the SPA app.
```

Open `https://agent-platform.example.com` (accept the self-signed
certificate warning) and sign in.

**Teardown:** `kind delete cluster --name agent-platform`. That
removes the cluster, the Postgres data, and the images loaded into it.
(`deploy/local/run.sh down` only removes the container stack.)

**Status of this path:** written from the manifests, not yet run end
to end — no cluster is available on this VM. The pieces it relies on
(images, Secret/ConfigMap wiring, Ingress routing) are the same ones
the container run and the Terraform EKS workflow already use, but the
kind-specific steps (ingress controller install, self-signed TLS,
`/etc/hosts`) are unverified until someone runs them. If a step
breaks, that is a real finding — bring it back and the guide gets
fixed.

## Troubleshooting

- **API pods / process exit at startup with an AUTH error:** the auth
  config is fail-closed. Either `AUTH_ISSUER` + `AUTH_AUDIENCE` are
  unset (set them), or they are set to placeholders (set your tenant).
  Check with `kubectl -n agent-platform logs deploy/agent-api`, or
  the process output in Way 1.
- **"Sign-in isn't configured" in the UI:** the bundle was built
  without `VITE_OIDC_ISSUER` / `VITE_OIDC_CLIENT_ID`. Rebuild it
  (Way 1: restart `npm run dev` with them set; Ways 2/3: rebuild the
  image — a UI settings change always needs a rebuild).
- **Login redirects back with an error / Okta rejects the redirect:**
  the origin is not registered in Okta, or not registered exactly
  (scheme + host + port must match `window.location.origin`).
- **Bedrock calls start failing (Way 2):** the short-lived SSO
  credentials expired — re-run `run.sh up` for fresh ones.
- **Ingress returns 404 (Way 3):** the Host header does not match
  `agent-platform.example.com` — check the `/etc/hosts` entry and
  that you are opening the https URL, not an IP or localhost.
- **UI shows "API offline" (Way 2/3):** the UI image serves only
  static files; `/api/*` must reach the API through the front door
  (Way 2) or the Ingress (Way 3). Check the front-door / Ingress
  routing before suspecting the API.
