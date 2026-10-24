# Generic IAM role factory. Each platform stack passes its own trust
# policy: Lambda execution roles, EKS cluster roles, and EKS node roles
# all share this shape but differ in who assumes them — which is why the
# trust policy is an input, not a hardcoded value.

resource "aws_iam_role" "this" {
  name               = var.name
  assume_role_policy = var.assume_role_policy

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "managed" {
  for_each   = toset(var.managed_policy_arns)
  role       = aws_iam_role.this.name
  policy_arn = each.value
}

resource "aws_iam_role_policy" "bedrock_invoke" {
  count = var.enable_bedrock_invoke ? 1 : 0
  name  = "${var.name}-bedrock-invoke"
  role  = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
      Resource = var.bedrock_model_arns
    }]
  })
}
