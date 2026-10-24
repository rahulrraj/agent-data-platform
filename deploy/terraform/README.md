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

## State backend

Local state by default (no backend block — do not commit state files).
For shared/team state, add this block to the stack's `versions.tf`
(replacing the local default) and create the bucket + lock table once,
outside Terraform:

```hcl
terraform {
  backend "s3" {
    bucket         = "agent-data-platform-tfstate"
    key            = "aws-lambda/terraform.tfstate" # or aws-eks/...
    region         = "us-east-1"
    dynamodb_table = "agent-data-platform-tf-locks"
    encrypt        = true
  }
}
```

Remote state resources are intentionally NOT created by this code.

## First deployment (manual, no pipeline)

```bash
cd deploy/terraform/stacks/aws-lambda   # or aws-eks

# 1. Build and push the image FIRST (the stack references it by tag).
#    Lambda:  docker build -f Dockerfile.lambda -t <ecr-url>:latest .
#    EKS:     docker build -t <ecr-api-url>:latest .   (repo root)
#             docker build -f src/ui/Dockerfile -t <ecr-ui-url>:latest .
#    Push with: aws ecr get-login-password | docker login ...
#    (The ECR URL comes from a first `terraform apply` with no image yet
#    only if you apply twice — simpler: apply once to create the repos,
#    push the images, then apply again with the tag.)

# 2. Apply. Secrets come from TF_VAR_* env vars, never committed values.
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
| `TF_VAR_data_connections` | Secret (optional) | DATA_CONNECTIONS JSON, if used. |

Until the OIDC role exists, the deploy workflows fail at the AWS auth
step — that is expected, and the test jobs still run. The workflows are
safe to merge before the account is wired in.

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
- **Base `Dockerfile` does not copy `registry/`** — the EKS image (repo
  root) needs `CAPABILITIES_PATH` overridden or the registry mounted
  until that is fixed; `Dockerfile.lambda` already copies it.
- **EKS public API endpoint** — `endpoint_public_access = true` is a
  dev convenience for CI kubectl; restrict it (private endpoint +
  bastion/VPN) for production.
