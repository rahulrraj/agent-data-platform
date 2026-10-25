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

module "networking" {
  source = "../../modules/networking"

  name = local.name_prefix
  tags = local.common_tags
}

module "postgres" {
  source = "../../modules/postgres"

  name                       = local.name_prefix
  db_name                    = var.db_name
  username                   = var.db_username
  password                   = var.db_password
  deletion_protection        = var.deletion_protection
  skip_final_snapshot        = var.skip_final_snapshot
  subnet_ids                 = module.networking.private_subnet_ids
  vpc_id                     = module.networking.vpc_id
  allowed_security_group_ids = [aws_security_group.lambda.id]
  tags                       = local.common_tags
}

module "ecr" {
  source = "../../modules/ecr"

  name = "${local.name_prefix}-api"
  tags = local.common_tags
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
  tags                  = local.common_tags
}

module "frontend" {
  source = "../../modules/frontend"

  name = local.name_prefix
  tags = local.common_tags
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
  architectures = ["x86_64"]

  vpc_config {
    subnet_ids         = module.networking.private_subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      CONVERSATION_STORE   = "postgres"
      POSTGRES_DSN         = module.postgres.dsn
      AGENT_MODEL_PROVIDER = var.model_provider
      GEMINI_API_KEY       = var.gemini_api_key
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
