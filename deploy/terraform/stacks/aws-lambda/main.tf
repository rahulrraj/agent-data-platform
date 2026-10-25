# AWS Lambda stack: the same container image + env vars as every other
# platform, behind a Lambda Function URL (NOT API Gateway — its 29s cap
# cannot serve agent turns that run minutes). See ../../README.md.

locals {
  # The stack suffix keeps this stack's resource names distinct from the
  # aws-eks stack's: both stacks are co-deployable in one account (RDS
  # identifier and ECR repo names would otherwise collide). The deploy
  # workflow's ECR repo names must match this prefix exactly.
  name_prefix = "${var.project}-${var.environment}-lambda"
  common_tags = merge(var.tags, {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
    Stack       = "aws-lambda"
  })
}

# Account ID is needed only to scope the Bedrock invoke policy: a
# cross-region inference profile (the default BEDROCK_MODEL_ID) is
# addressed by an account-qualified inference-profile ARN, while a plain
# foundation-model ID uses the unqualified foundation-model ARN. Both
# forms are granted, for the two configured models only — never "*".
data "aws_caller_identity" "current" {}

locals {
  bedrock_model_arns = var.bedrock_model_arns != null ? var.bedrock_model_arns : flatten([
    for model in [var.bedrock_model_id, var.bedrock_embed_model] : [
      "arn:aws:bedrock:${var.aws_region}::foundation-model/${model}",
      "arn:aws:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:inference-profile/${model}",
    ]
  ])
}

module "networking" {
  source = "../../modules/networking"

  name                 = local.name_prefix
  cidr_block           = var.vpc_cidr_block
  az_count             = var.az_count
  enable_nat           = var.enable_nat
  nat_per_az           = var.nat_per_az
  enable_vpc_endpoints = var.enable_vpc_endpoints
  tags                 = local.common_tags
}

module "postgres" {
  source = "../../modules/postgres"

  name                       = local.name_prefix
  engine_version             = var.db_engine_version
  instance_class             = var.db_instance_class
  allocated_storage          = var.db_allocated_storage
  db_name                    = var.db_name
  username                   = var.db_username
  password                   = var.db_password
  multi_az                   = var.db_multi_az
  deletion_protection        = var.deletion_protection
  skip_final_snapshot        = var.skip_final_snapshot
  backup_retention_period    = var.backup_retention_period
  subnet_ids                 = module.networking.private_subnet_ids
  vpc_id                     = module.networking.vpc_id
  allowed_security_group_ids = [aws_security_group.lambda.id]
  tags                       = local.common_tags
}

module "ecr" {
  source = "../../modules/ecr"

  name                   = "${local.name_prefix}-api"
  force_delete           = var.ecr_force_delete
  image_tag_mutability   = var.ecr_image_tag_mutability
  keep_image_count       = var.ecr_keep_image_count
  tags                   = local.common_tags
}

module "iam" {
  source = "../../modules/iam"

  name = "${local.name_prefix}-lambda-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  managed_policy_arns = [
    "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
  ]
  enable_bedrock_invoke = var.enable_bedrock_invoke
  bedrock_model_arns    = local.bedrock_model_arns
  tags                  = local.common_tags
}

module "frontend" {
  source = "../../modules/frontend"

  name              = local.name_prefix
  force_destroy     = var.frontend_force_destroy
  enable_versioning = var.frontend_enable_versioning
  access_log_bucket = var.frontend_access_log_bucket
  tags              = local.common_tags
}

# Lambda needs a security group for VPC access (egress only; the function
# initiates all connections — to RDS, and to model APIs via the NAT).
resource "aws_security_group" "lambda" {
  name        = "${local.name_prefix}-lambda-sg"
  description = "Lambda function egress"
  vpc_id      = module.networking.vpc_id

  egress {
    description = "All egress (RDS, model APIs)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-lambda-sg" })
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.name_prefix}-api"
  retention_in_days = 14

  tags = local.common_tags
}

resource "aws_lambda_function" "api" {
  function_name = "${local.name_prefix}-api"
  role          = module.iam.role_arn
  package_type  = "Image"
  image_uri     = "${module.ecr.repository_url}:${var.image_tag}"
  timeout       = 900
  memory_size   = var.lambda_memory_mb
  # The image MUST be built for this architecture: plain `docker build`
  # on an arm64 host produces an arm64 image that fails on this x86_64
  # function. The deploy workflow passes --platform linux/amd64.
  architectures = ["x86_64"]

  # Concurrency ceiling for the small RDS instance (m-32): each
  # execution can hold its own Postgres connection, and a db.t4g.micro
  # has on the order of ~100 connections. 10 concurrent executions is a
  # safe dev ceiling; raise it (and the instance class, or add RDS
  # Proxy) together before any load-bearing use.
  reserved_concurrent_executions = var.lambda_reserved_concurrency

  # Cross-field validation (m-22): Terraform variable validation blocks
  # can only reference their own variable, so provider/key consistency
  # is enforced here, where both sides are visible. These fail the plan
  # instead of applying cleanly and failing on the first model call.
  lifecycle {
    precondition {
      condition     = var.model_provider != "gemini" || var.gemini_api_key != ""
      error_message = "model_provider is gemini but gemini_api_key is empty — set TF_VAR_gemini_api_key (a deploy with no key applies cleanly and then fails every model call with an auth error)."
    }
    precondition {
      condition     = var.model_provider != "bedrock" || var.enable_bedrock_invoke
      error_message = "model_provider is bedrock but enable_bedrock_invoke is false — the function would get AccessDenied on the first model call. Set -var=enable_bedrock_invoke=true (the deploy workflow does this automatically for the bedrock provider)."
    }
  }

  vpc_config {
    subnet_ids         = module.networking.private_subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      CONVERSATION_STORE   = "postgres"
      POSTGRES_DSN         = module.postgres.dsn
      AGENT_MODEL_PROVIDER = var.model_provider
      AGENT_FRAMEWORK      = var.agent_framework
      GEMINI_API_KEY       = var.gemini_api_key
      GEMINI_MODEL         = var.gemini_model
      GEMINI_EMBED_MODEL   = var.gemini_embed_model
      AWS_REGION           = var.aws_region
      BEDROCK_MODEL_ID     = var.bedrock_model_id
      BEDROCK_EMBED_MODEL  = var.bedrock_embed_model
      DATA_CONNECTIONS     = var.data_connections
      CORS_ORIGINS         = join(",", var.cors_origins)
      # /tmp is the only writable directory on Lambda; the embedding
      # cache rebuilds on cold start (shared cache is a follow-up).
      EMBEDDING_CACHE_PATH = "/tmp/embedding_cache.db"
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda]

  tags = local.common_tags
}

# Function URL, NOT API Gateway: 15-minute timeout and RESPONSE_STREAM
# mode. API Gateway's 29s integration timeout cannot serve agent turns.
resource "aws_lambda_function_url" "api" {
  function_name      = aws_lambda_function.api.function_name
  authorization_type = var.function_url_auth
  invoke_mode        = "RESPONSE_STREAM"
}
