# AWS EKS stack: deploys the application onto an EXISTING EKS cluster.
# The cluster itself — its control plane, node groups, addons, VPC and
# subnets — is NOT created here. It already exists, owned outside this
# stack, and is taken as an input (var.cluster_name, read back through
# the aws_eks_cluster data source). Neither is Postgres: the database
# already exists and its DSN is supplied as the postgres_dsn variable,
# which the pipeline writes into the k8s Secret.
#
# What this stack DOES create (application resources only):
# - ECR repositories for the API + UI images. These stay: they are
#   app-scoped, not cluster infrastructure — the pipeline pushes the
#   images here, and the repos carry the per-stack name prefix (like
#   the Lambda stack's repo) so the two stacks stay co-deployable.
# - The cluster's OIDC identity provider + an IRSA role the API pods
#   assume for Bedrock (bedrock:InvokeModel, scoped to the two
#   configured models). The ServiceAccount itself lives in deploy/k8s/
#   (serviceaccount.yaml) and is annotated by the pipeline from the
#   irsa_role_arn output — the workload stays defined by the manifests.
#
# The workload itself stays defined by deploy/k8s/ — this stack does
# NOT duplicate the Deployment/Service/Ingress spec. The deploy
# pipeline applies those manifests to this cluster with the ECR image
# URLs substituted in.
#
# Differences from the Lambda stack, stated plainly:
# - The UI runs in-cluster (nginx, per deploy/k8s/ui-deployment.yaml),
#   so this stack does NOT create the S3 + CloudFront frontend module.
# - The in-cluster Postgres StatefulSet in deploy/k8s is NOT applied:
#   Postgres already exists outside this stack; its DSN comes in as
#   the postgres_dsn variable and the pipeline points the k8s Secret
#   at it.
# - Bedrock access for pods is IRSA (OIDC provider + service-account
#   role, below). The Lambda stack covers Bedrock via its execution
#   role.

locals {
  # The stack suffix keeps this stack's resource names distinct from the
  # aws-lambda stack's: both stacks are co-deployable in one account
  # (ECR repo names would otherwise collide). The deploy workflow's
  # ECR repo names must match this prefix exactly.
  name_prefix = "${var.project}-${var.environment}-eks"
  common_tags = merge(var.tags, {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
    Stack       = "aws-eks"
  })
}

# The existing cluster: name in, endpoint/CA/OIDC issuer back out.
# If the name is wrong the plan fails here, before creating anything.
data "aws_eks_cluster" "this" {
  name = var.cluster_name
}

# Account ID is needed only to scope the Bedrock invoke policy: a
# cross-region inference profile (the default BEDROCK_MODEL_ID) is
# addressed by an account-qualified inference-profile ARN, while a plain
# foundation-model ID uses the unqualified foundation-model ARN. Both
# forms are granted, for the two configured models only — never "*".
# (Same computation as the Lambda stack.)
data "aws_caller_identity" "current" {}

locals {
  bedrock_model_arns = var.bedrock_model_arns != null ? var.bedrock_model_arns : flatten([
    for model in [var.bedrock_model_id, var.bedrock_embed_model] : [
      "arn:aws:bedrock:${var.aws_region}::foundation-model/${model}",
      "arn:aws:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:inference-profile/${model}",
    ]
  ])

  # The OIDC issuer host (no scheme) is the trust-policy condition key
  # prefix for IRSA: "<issuer-host>:sub" / "<issuer-host>:aud".
  oidc_issuer_host = trimprefix(data.aws_eks_cluster.this.identity[0].oidc[0].issuer, "https://")
}

module "ecr_api" {
  source = "../../modules/ecr"

  name                 = "${local.name_prefix}-api"
  force_delete         = var.ecr_force_delete
  image_tag_mutability = var.ecr_image_tag_mutability
  keep_image_count     = var.ecr_keep_image_count
  tags                 = local.common_tags
}

module "ecr_ui" {
  source = "../../modules/ecr"

  name                 = "${local.name_prefix}-ui"
  force_delete         = var.ecr_force_delete
  image_tag_mutability = var.ecr_image_tag_mutability
  keep_image_count     = var.ecr_keep_image_count
  tags                 = local.common_tags
}

# IRSA: the OIDC identity provider for the existing cluster. One
# provider per cluster (per issuer URL) — if another stack or process
# already manages this cluster's provider, this resource conflicts
# with it (EntityAlreadyExists) and must be imported or removed, not
# duplicated.
resource "aws_iam_openid_connect_provider" "this" {
  url = data.aws_eks_cluster.this.identity[0].oidc[0].issuer

  client_id_list = ["sts.amazonaws.com"]

  # Well-known EKS OIDC thumbprint (the root CA all EKS OIDC issuers
  # share). Pinned instead of resolving it per apply via the tls
  # provider, so this stack needs no extra provider.
  thumbprint_list = ["9e99a48a9960b14926bb7f3b02e22da2b0ab7280"]

  tags = local.common_tags
}

# IRSA role for the API pods: assumed via the ServiceAccount
# deploy/k8s/serviceaccount.yaml (namespace agent-platform, name
# agent-api) — the subject below must match that file exactly.
# No managed policies: pods need nothing from AWS except Bedrock
# invoke, which the iam module attaches when enabled.
module "iam_api_irsa" {
  source = "../../modules/iam"

  name = "${local.name_prefix}-api-irsa-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.this.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${local.oidc_issuer_host}:sub" = "system:serviceaccount:agent-platform:agent-api"
          "${local.oidc_issuer_host}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })
  enable_bedrock_invoke = var.enable_bedrock_invoke
  bedrock_model_arns    = local.bedrock_model_arns
  tags                  = local.common_tags
}

# Cross-field validation (m-22): Terraform variable validation blocks
# can only reference their own variable, so provider/policy
# consistency is enforced here, where both sides are visible. This
# fails the plan instead of applying cleanly and failing on the first
# model call. (Same rule as the Lambda stack's function
# precondition; there is no function resource here, so the check
# lives on a bare terraform_data resource.)
resource "terraform_data" "bedrock_precondition" {
  lifecycle {
    precondition {
      condition     = var.model_provider != "bedrock" || var.enable_bedrock_invoke
      error_message = "model_provider is bedrock but enable_bedrock_invoke is false — the pods would get AccessDenied on the first model call. Set -var=enable_bedrock_invoke=true (the deploy workflow does this automatically for the bedrock provider)."
    }
  }
}
