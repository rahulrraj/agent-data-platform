# Deploying on Kubernetes (any cloud)

The same manifests run on EKS, GKE, AKS, or on-prem — that is the
cloud-agnostic story. You need a cluster, an ingress controller, and a
container registry.

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
  --from-literal=POSTGRES_DSN='postgres://agent:agent@postgres:5432/agentdb' \
  --from-literal=DATA_CONNECTIONS='{"snowflake_analytics": {...}}' \
  --from-literal=GEMINI_API_KEY='' \
  --dry-run=client -o yaml | kubectl apply -f -
```

For production, use external-secrets or your cloud's secret manager instead
of the checked-in `secret.yaml` template.

## 4. Apply

```bash
kubectl apply -k deploy/k8s/
```

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
- **TLS**: add your cert-manager annotations to `ingress.yaml`.
