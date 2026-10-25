# Terraform infrastructure for agent-data-platform

Infrastructure as code for the AWS deployment paths. The app itself is
platform-agnostic — one container image plus env vars — so each platform
stack runs the same image and only differs in how it is hosted.

## Layout

```text
deploy/terraform/
├── modules/                  # Shared, reusable pieces
│   ├── networking/           # VPC, public/private subnets, NAT
│   ├── postgres/             # RDS Postgres (conversation store)
│   ├── ecr/                  # Container image repository
│   ├── iam/                  # Generic IAM role factory
│   └── frontend/             # S3 bucket + CloudFront (static UI)
├── stacks/
│   ├── aws-lambda/           # Serverless: Lambda + Function URL
│   └── aws-eks/              # Kubernetes: EKS cluster + node group
└── README.md                 # This file
```

A stack composes modules and adds only its own platform-specific
resources (the Lambda function + Function URL, the EKS cluster + node
group). The workload itself is never duplicated in Terraform:

- Lambda stack: the function runs the `Dockerfile.lambda` image.
- EKS stack: the workload stays defined by `deploy/k8s/` — the pipeline
  applies those manifests to the cluster with the ECR image URLs
  substituted in. The in-cluster Postgres StatefulSet is NOT applied
  (RDS from the postgres module replaces it), and the k8s Secret is
  created from Terraform outputs instead of the placeholder template.

## Prerequisites

- An AWS account, AWS CLI v2, Terraform >= 1.5, Docker.
- For the Lambda stack: nothing else. For the EKS stack: `kubectl`, and
  an NGINX ingress controller installed in the cluster before the
  `deploy/k8s/ingress.yaml` Ingress routes traffic (the manifests assume
  one exists; installing it is a one-time cluster step, not Terraform).
- GitHub Actions deploys (see below) additionally need the OIDC setup.
- The remote-state bucket + lock table below (one-time, out-of-band).

The two stacks are co-deployable in one account: each stack's
`name_prefix` includes the stack (`…-lambda` / `…-eks`), so RDS
identifiers and ECR repo names do not collide. (Before this, both
stacks shared `project-environment` and the second apply failed on
duplicates — the stacks were alternatives, not co-deployable.)

## State backend

Both stacks use the S3 backend in their `versions.tf` — this is
required, not optional: CI runners are ephemeral, so local state would
be lost between runs and the second deploy would try to re-create
existing resources. Backend blocks cannot use variables, so the values
below are literals in the blocks; if your account uses a different
bucket/table/region, change them in `versions.tf` (and only there) and
keep the workflows' plain `terraform init` (no `-backend-config`
flags — the block is the single source of truth).

The bucket + lock table are created OUT-OF-BAND, once, outside
Terraform — this code deliberately never creates them (a stack cannot
own the backend it stores its own state in):

```bash
aws s3api create-bucket --bucket agent-data-platform-tfstate --region us-east-1
aws s3api put-bucket-versioning --bucket agent-data-platform-tfstate \
  --versioning-configuration Status=Enabled
aws dynamodb create-table --table-name agent-data-platform-tf-locks \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST --region us-east-1
```

Per-stack keys: `aws-lambda/terraform.tfstate` and
`aws-eks/terraform.tfstate`, region `us-east-1`, `encrypt = true`.
Do not commit state files (`.gitignore` covers `*.tfstate*`,
`.terraform/`, `*.tfplan`). If you previously used local state, run
`terraform init -migrate-state` once to move it into this backend.

## Lambda stack: auth, CORS, and secrets

- **Function URL auth defaults to `AWS_IAM`.** The app itself has no
  auth middleware, so a public (`NONE`) Function URL exposes every
  registry capability — and the service account's credentials — to the
  internet. `NONE` is dev-only: use it only for a throwaway
  deployment, never for anything reachable beyond your own testing.
  Consequence to plan for: a browser cannot sign `AWS_IAM` requests,
  so with the default the CloudFront UI cannot call the Function URL
  directly — it needs a signing front door (or a separately
  authenticated API path) before browser traffic works.
- **CORS:** set the stack's `cors_origins` variable to the real UI
  origin(s) (e.g. the CloudFront `ui_url`); it is passed to the
  function as `CORS_ORIGINS` (comma-joined). Leaving it empty keeps
  the app's own default.
- **Secrets:** `data_connections` is marked `sensitive` (redacted from
  plan/apply output), but it still lands as a plaintext Lambda
  environment variable and in Terraform state. For production, prefer
  Secrets Manager / SSM Parameter Store for Lambda secrets (the
  function reads the secret at startup) over passing them through this
  variable. The same applies to `gemini_api_key` and `db_password`:
  sensitive in Terraform is redaction, not encryption at rest in the
  places the values are consumed.

## RDS posture

The shared `postgres` module sets `backup_retention_period = 7` and
`storage_type = "gp3"`. Each stack exposes `deletion_protection`
(default `false`) and `skip_final_snapshot` (default `true`) so
production can set `deletion_protection = true`,
`skip_final_snapshot = false` without forking the module; dev keeps
clean teardown.

## First deployment (manual, no pipeline)

```bash
cd deploy/terraform/stacks/aws-lambda   # or aws-eks

# 1. Create the ECR repo(s) FIRST (idempotent), then build and push —
#    the Lambda function / k8s image reference must exist at apply time.
#    Lambda repo: agent-data-platform-dev-lambda-api
#    EKS repos:   agent-data-platform-dev-eks-api, agent-data-platform-dev-eks-ui
#    (project-environment-stack-image; see the stack's name_prefix.)
aws ecr create-repository --repository-name <repo> --region us-east-1
#    Lambda:  docker build -f Dockerfile.lambda -t <ecr-url>:latest .
#    EKS:     docker build -t <ecr-api-url>:latest .   (repo root)
#             docker build -f src/ui/Dockerfile -t <ecr-ui-url>:latest .
#    Push with: aws ecr get-login-password | docker login ...

# 2. Apply. Secrets come from TF_VAR_* env vars, never committed values.
#    Note: an explicitly empty TF_VAR_data_connections overrides the
#    "{}" default with "" and breaks Lambda startup — leave it unset
#    (or set it to real JSON) rather than exporting it empty.
export TF_VAR_db_password='...'
export TF_VAR_gemini_api_key='...'   # Lambda stack, gemini provider only
terraform init
terraform apply
```

Lambda stack outputs: `function_url` (the API, chat included),
`ui_url` (CloudFront), `ecr_repository_url`, `rds_endpoint`.
EKS stack outputs: `cluster_name` (for `aws eks update-kubeconfig`),
`ecr_api_repository_url`, `ecr_ui_repository_url`, `rds_endpoint`,
`rds_dsn` (sensitive — feed it to the k8s Secret, see below).

EKS manifest deploy (after `terraform apply` + `aws eks update-kubeconfig`):

```bash
# Substitute the ECR image URLs into temp copies (never edit the
# committed manifests for this), set the provider, skip the in-cluster
# Postgres (RDS replaces it) and the placeholder Secret (created below).
cp -r deploy/k8s /tmp/k8s-deploy
sed -i "s|agent-data-platform:latest|<ecr-api-url>:<tag>|" /tmp/k8s-deploy/api-deployment.yaml
sed -i "s|agent-ui:latest|<ecr-ui-url>:<tag>|" /tmp/k8s-deploy/ui-deployment.yaml
sed -i 's|AGENT_MODEL_PROVIDER: "ollama"|AGENT_MODEL_PROVIDER: "gemini"|' /tmp/k8s-deploy/configmap.yaml
sed -i 's|host: agent-platform.example.com|host: your-domain.example.com|' /tmp/k8s-deploy/ingress.yaml
kubectl apply -f /tmp/k8s-deploy/namespace.yaml \
              -f /tmp/k8s-deploy/configmap.yaml \
              -f /tmp/k8s-deploy/api-deployment.yaml \
              -f /tmp/k8s-deploy/ui-deployment.yaml \
              -f /tmp/k8s-deploy/ingress.yaml
kubectl -n agent-platform create secret generic agent-platform-secrets \
  --from-literal=POSTGRES_DSN="$(terraform output -raw rds_dsn)" \
  --from-literal=DATA_CONNECTIONS='{}' \
  --from-literal=GEMINI_API_KEY="$GEMINI_API_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n agent-platform rollout status deployment/agent-api
kubectl -n agent-platform rollout status deployment/agent-ui
```

## GitHub Actions + OIDC (pipeline prerequisites)

Deploys use GitHub OIDC, never static AWS keys. One-time setup:

1. In the AWS account, create the GitHub OIDC identity provider
   (`token.actions.githubusercontent.com`, audience `sts.amazonaws.com`)
   if it does not exist.
2. Create an IAM role trusted by that provider for this repo, e.g.:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "arn:aws:iam::<account>:oidc-provider/token.actions.githubusercontent.com" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
      "StringLike": { "token.actions.githubusercontent.com:sub": "repo:rahulrraj/agent-data-platform:*" }
    }
  }]
}
```

Attach the permissions the deploy needs: ECR push/pull, Lambda create/update,
Terraform's EC2/VPC/RDS/EKS/IAM/CloudFront/S3 scope for the stacks, and
`eks:AccessKubernetes` equivalent for kubectl (or cluster admin via
`aws_eks_access_entry` — see the EKS follow-ups). Least privilege per
environment is a follow-up; start with a scoped deploy role, not
AdministratorAccess.

3. Set these in the GitHub repo (Settings → Secrets and variables →
   Actions):

| Name | Kind | Purpose |
| ---- | ---- | ------- |
| `AWS_ROLE_TO_ASSUME` | Variable | IAM role ARN for OIDC (not secret, but a variable keeps it out of files). |
| `AWS_REGION` | Variable | e.g. `us-east-1`. |
| `TF_VAR_db_password` | Secret | RDS master password for both stacks. |
| `TF_VAR_gemini_api_key` | Secret | GEMINI_API_KEY for the Lambda stack when provider is gemini. |
| `TF_VAR_data_connections` | Secret (optional) | DATA_CONNECTIONS JSON, if used. Leave unset (not empty) when unused — the workflows default an empty value to `{}`. |
| `INGRESS_HOST` | Variable | Domain for the EKS Ingress host (deploy-eks only). Set to a real domain; the fallback is the `agent-platform.example.com` placeholder, which routes no real traffic. |

Deploy gates (manual repo-settings step, do this before the first real
deploy): create the `deploy-lambda` and `deploy-eks` environments in
the repo (Settings → Environments) with required reviewers. The
workflows split plan → review → apply: the plan job uploads the
Terraform plan as an artifact, and the apply job (which carries the
`environment:`) applies exactly that reviewed plan — there is no bare
`-auto-approve`. GitHub auto-creates a referenced environment with NO
protection if it does not exist yet, so the workflows are safe to merge
before this step — but until reviewers are configured, the gate is not
real. Deploys also only trigger on their own stack's paths (plus the
shared modules), so a docs-only or backend-only push does not apply
infrastructure.

Until the OIDC role exists, the deploy workflows fail at the AWS auth
step — that is expected, and the test jobs still run. The workflows are
safe to merge before the account is wired in.

## How the pipelines wire the UI to the API

- Lambda: the apply job reads the stack's `function_url` output and
  builds the UI with `VITE_API_URL=<function_url>` (trailing slash
  trimmed) before syncing to S3 + invalidating CloudFront. Building
  without it leaves the UI calling relative `/api/*` against S3, which
  returns HTML, not JSON.
- EKS: no `VITE_API_URL` is needed — the Ingress routes `/api/*` to
  the API service. The workflow substitutes `INGRESS_HOST` into the
  Ingress and then checks (non-fatally) that the Ingress has a
  load-balancer address and that `/api/health` answers through it.

## How to extend: adding a platform (GCP and beyond)

The pattern is: one stack per platform, same image, same env vars,
same module interface. A new stack must provide:

1. **The image** — the same container image (repo-root `Dockerfile` for
   serverful paths, `Dockerfile.lambda`-equivalent for serverless ones),
   from a registry the platform can pull from (ECR here; Artifact
   Registry on GCP).
2. **The env vars** — the full app config surface (`CONVERSATION_STORE`,
   `POSTGRES_DSN`, `AGENT_MODEL_PROVIDER`, provider keys/roles,
   `EMBEDDING_CACHE_PATH`, `CAPABILITIES_PATH`). No app-config forks:
   if a platform needs a new variable, it goes through `settings.py`
   like every other variable.
3. **The database** — a managed Postgres the platform can reach
   (RDS here; Cloud SQL on GCP). Reuse `modules/postgres` on AWS; on
   GCP the equivalent is a Cloud SQL module with the same outputs
   (`endpoint`, `dsn`).
4. **The frontend** — static UI hosting (S3 + CloudFront here; Cloud
   Storage + Cloud CDN on GCP). Reuse `modules/frontend` on AWS; the
   GCP equivalent keeps the same outputs (`bucket_name`,
   `distribution_id`, `ui_url`).
5. **The pipeline** — one new deploy workflow file (see
   `.github/workflows/`), reusing `reusable-build-test.yml`. No
   pipeline rewrite.

Concrete GCP mapping for later (not built):

| Path | AWS now | GCP later |
| ---- | ------- | --------- |
| Serverless API | Lambda + Function URL | Cloud Run (container, HTTP, streaming) |
| Kubernetes API | EKS + `deploy/k8s/` | GKE + the same `deploy/k8s/` manifests |
| Registry | ECR (`modules/ecr`) | Artifact Registry (same module shape) |
| Database | RDS (`modules/postgres`) | Cloud SQL (same outputs) |
| Frontend | S3 + CloudFront (`modules/frontend`) | Cloud Storage + Cloud CDN |
| Identity | IAM roles (`modules/iam`) | GCP service accounts (per-stack, like EKS roles) |

Modules that are genuinely platform-neutral (networking shapes, Postgres
semantics, frontend semantics) get a per-cloud counterpart with the same
outputs; modules whose trust model is platform-specific (IAM roles vs
GCP service accounts) live per stack. Do not force one module to cover
two clouds' identity systems.

## TODO: deployment strategies (future work, not implemented)

This section is a NOTE for future decisions, per platform. Nothing here
is implemented; the pipelines currently deploy straight to the live
target (Lambda: new image → new invocations; EKS: rolling update via
`kubectl apply`, which is the Kubernetes default).

- **Rolling** — the Kubernetes default: `kubectl apply` rolls pods
  gradually (the existing manifests already behave this way; tune
  `maxSurge`/`maxUnavailable` when it matters). On Lambda, rolling is
  automatic in effect: new invocations pick up the new version, and
  in-flight invocations finish on the old one.
- **Blue-green** — on Lambda, this maps to aliases with weighted routing
  (two versions live, traffic shifted by weight). On EKS, it needs a
  second Deployment + Service cutover, which the plain manifests do not
  provide — decide per release whether the extra machinery is worth it.
- **Canary** — on Lambda, aliases with weighted routing (e.g. 10% of
  traffic to the new version, then shift). On EKS/k8s, canary needs an
  ingress- or mesh-level mechanism (e.g. Argo Rollouts or Flagger)
  beyond the plain manifests — that tooling is not installed or
  configured here.
- **Static UI on CloudFront is a separate concern** — it deploys by
  object sync + cache invalidation, independent of the API strategy.
  A UI canary would mean a second CloudFront behavior/origin, not an
  API routing change.

Decide per platform before building any of this: which strategy the
Lambda stack and the EKS stack each use, and whether the canary
tooling (Argo Rollouts/Flagger) earns its place on EKS.

## Known follow-ups (not built here)

- **True incremental chat streaming on Lambda** — Mangum buffers the
  whole SSE body; Function URL `RESPONSE_STREAM` mode plus a streaming
  runtime path is needed (see the Lambda README section).
- **Shared embedding cache** — the sqlite cache under `/tmp` rebuilds
  on every Lambda cold start; a shared cache (e.g. ElastiCache/Redis)
  is the follow-up.
- **IRSA for Bedrock on EKS** — pods currently get model access via
  Secrets (gemini key) or not at all; the correct Bedrock pattern is an
  OIDC provider + service-account role.
- **EKS RDS ingress is VPC-CIDR based** — tighten to the node security
  group via a launch template if needed.
- **EKS public API endpoint** — `endpoint_public_access = true` is a
  dev convenience for CI kubectl; restrict it (private endpoint +
  bastion/VPN) for production.
