# Deploying on Kubernetes (any cloud)

The same manifests run on EKS, GKE, AKS, or on-prem — that is the
cloud-agnostic story. You need a cluster, an ingress controller, and a
container registry.

## ⚠ Read before `kubectl apply -k`

`kubectl apply -k deploy/k8s/` applies **everything** in the
kustomization, including two things you usually do NOT want:

- **`secret.yaml` — the placeholder Secret.** It is a local-only
  template (untracked in git; in a fresh clone it does not exist at
  all, so kustomize fails on the missing file until you recreate it
  from §3 or skip it). Its DSN password is deliberately
  non-functional (`REPLACE-ME-BEFORE-APPLYING`). Applying it as-is
  gives pods that cannot connect — and anyone who once applied the
  old tracked version got the well-known default password
  `agent:agent`. Create the real Secret via §3 instead.
- **`postgres-statefulset.yaml` — in-cluster Postgres.** The
  Terraform EKS workflow deliberately SKIPS this file (RDS from the
  postgres module is the conversation store) and creates the Secret
  from Terraform outputs instead. If you `apply -k` on a
  Terraform-managed cluster, you get a second, empty Postgres that
  nothing should point at — and a Secret that points at it.

For a Terraform-managed cluster, use the selective apply in
`deploy/terraform/README.md` (namespace → Secret → configmap →
Deployments → Ingress), never the bare `-k`. The bare `-k` is only
for a standalone dev cluster that genuinely wants in-cluster Postgres.

## ⚠ TLS: this template serves plaintext HTTP

`ingress.yaml` as committed has no `tls:` block and no cert-manager
annotation — the API is served over plaintext HTTP. That is fine for
a local/dev cluster, but **do not expose real traffic through it**:
install cert-manager, add the (commented) annotation + `tls:` block
in `ingress.yaml` with a real host and ClusterIssuer, and only then
point DNS at it. The Terraform EKS workflow substitutes the host but
adds no TLS either, so the same warning applies to that path.

## 1. Build and push the images (from the repo root)

```bash
docker build -t <registry>/agent-data-platform:latest .
docker build -f src/ui/Dockerfile -t <registry>/agent-ui:latest .
docker push <registry>/agent-data-platform:latest
docker push <registry>/agent-ui:latest
```

## 2. Point the manifests at your images and domain

- `api-deployment.yaml`: set the `image:` to your registry image.
- `ui-deployment.yaml`: same for the UI image.
- `ingress.yaml`: set `ingressClassName` to your controller
  (`nginx`, `gce`, `alb`, …) and `host:` to your domain.

## 3. Fill in secrets (never commit real values)

```bash
kubectl -n agent-platform create secret generic agent-platform-secrets \
  --from-literal=POSTGRES_DSN='postgres://agent:<password>@<host>:5432/agentdb' \
  --from-literal=DATA_CONNECTIONS='{"snowflake_analytics": {...}}' \
  --from-literal=GEMINI_API_KEY='' \
  --dry-run=client -o yaml | kubectl apply -f -
```

Create the Secret **before** applying the Deployments: the API pods
reference it via `secretKeyRef`, so Deployments applied first sit in
`CreateContainerConfigError` until the Secret appears (the deploy-eks
workflow orders it this way for the same reason). For production, use
external-secrets or your cloud's secret manager instead of the
local-only `secret.yaml` template.

## 4. Apply

Standalone dev cluster only (see the warnings at the top):

```bash
kubectl apply -k deploy/k8s/
```

Terraform-managed cluster: use the selective apply in
`deploy/terraform/README.md` instead — it skips the in-cluster
Postgres StatefulSet and the placeholder Secret.

## Notes

- **Postgres**: the included StatefulSet is for dev/small prod. For real
  workloads use RDS / Cloud SQL / Azure Database, drop
  `postgres-statefulset.yaml` from the kustomization, and point
  `POSTGRES_DSN` at the managed instance.
- **Ollama**: `OLLAMA_HOST` defaults to `http://ollama:11434` (in-cluster).
  Run Ollama in the cluster or point it at an external host. For Gemini,
  set `AGENT_MODEL_PROVIDER=gemini` and `GEMINI_API_KEY`.
- **Embedding cache**: each API pod keeps its own `embedding_cache.db`
  (emptyDir). It's a cache — cold pods re-embed on first discovery, then
  share nothing. At thousands of capabilities this is fine; the vectors
  are small and rebuildable.
- **TLS**: see the warning at the top — add the cert-manager
  annotations + `tls:` block from `ingress.yaml` before real use;
  as-is the Ingress serves plaintext HTTP.
- **AWS_REGION**: `configmap.yaml` sets it (the deploy-eks workflow
  substitutes the real region). A region alone grants no Bedrock
  access — pods need credentials via IRSA, a known follow-up.
- **Ports**: the UI container listens on 8080 (non-root
  nginx-unprivileged image); the Service maps 80 → 8080, so the
  Ingress is unchanged. The API container runs as UID 10001.
