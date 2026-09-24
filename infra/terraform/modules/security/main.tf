# Security layer: security groups referenced by group id (never by CIDR on the
# data tier) and the two IAM roles an ECS task uses.
#
# The split that matters (docs/architecture/deployment.md sections 6 and 9):
#
#   execution role -> pulls the image, writes the container logs, and resolves
#                     the `secrets`/SSM values ECS injects before the task starts
#   task role      -> what the *running* application may do: read its own
#                     secrets, read and write its own bucket prefix, publish
#                     traces. It cannot touch any other bucket, secret or service.
#
# Every action is named. The only wildcard resources are the ones AWS requires
# (ecr:GetAuthorizationToken and xray:PutTraceSegments have no resource-level
# permissions); those statements carry a comment saying so.

locals {
  name_prefix = "${var.project}-${var.environment}"
  account_id  = data.aws_caller_identity.current.account_id

  # ECR repositories are named ${name_prefix}-api and ${name_prefix}-web by the
  # ecs module, so the execution role can be scoped to the naming convention
  # without a dependency edge back from this module.
  ecr_repository_arns = [
    "arn:aws:ecr:${var.aws_region}:${local.account_id}:repository/${local.name_prefix}-*",
  ]

  # Log groups are /ecs/${name_prefix}/<service>; PutLogEvents wants a
  # log-stream suffix, which the trailing ":*" supplies.
  log_group_arn_patterns = [
    "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/ecs/${local.name_prefix}/*:*",
  ]
}

data "aws_caller_identity" "current" {}

# --- security groups ---------------------------------------------------------

# Internet-facing. The only group with 0.0.0.0/0 ingress, and only on 80/443.
resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-alb-sg"
  description = "Public ALB: HTTPS and HTTP in, app and web ports out"
  vpc_id      = var.vpc_id

  egress = []

  tags = merge(var.tags, { Name = "${local.name_prefix}-alb-sg" })
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  for_each = toset(var.alb_ingress_cidrs)

  security_group_id = aws_security_group.alb.id
  description       = "HTTP from ${each.value}"
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  for_each = toset(var.alb_ingress_cidrs)

  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from ${each.value}"
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "alb_to_app" {
  security_group_id            = aws_security_group.alb.id
  description                  = "ALB to API tasks"
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = var.app_port
  to_port                      = var.app_port
}

resource "aws_vpc_security_group_egress_rule" "alb_to_web" {
  security_group_id            = aws_security_group.alb.id
  description                  = "ALB to web tasks"
  referenced_security_group_id = aws_security_group.web.id
  ip_protocol                  = "tcp"
  from_port                    = var.web_port
  to_port                      = var.web_port
}

# Application tier. Accepts the app port from the ALB group only; egress is left
# at the AWS default because tasks must reach LLM providers through the NAT and
# the list of provider CIDRs is not stable. The security boundary here is
# ingress: nothing reaches these tasks except the load balancer.
resource "aws_security_group" "app" {
  name        = "${local.name_prefix}-app-sg"
  description = "ECS API and migration tasks: app port from the ALB only"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = "${local.name_prefix}-app-sg" })
}

resource "aws_vpc_security_group_ingress_rule" "app_from_alb" {
  security_group_id            = aws_security_group.app.id
  description                  = "API port from the ALB security group"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = var.app_port
  to_port                      = var.app_port
}

resource "aws_security_group" "web" {
  name        = "${local.name_prefix}-web-sg"
  description = "ECS web tasks: web port from the ALB only"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = "${local.name_prefix}-web-sg" })
}

resource "aws_vpc_security_group_ingress_rule" "web_from_alb" {
  security_group_id            = aws_security_group.web.id
  description                  = "Web port from the ALB security group"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = var.web_port
  to_port                      = var.web_port
}

# Data tier. No ingress from a CIDR at all, and egress deliberately empty: a
# database has no reason to open a connection outward.
resource "aws_security_group" "rds" {
  name        = "${local.name_prefix}-rds-sg"
  description = "RDS PostgreSQL: 5432 from the application security group only"
  vpc_id      = var.vpc_id

  egress = []

  tags = merge(var.tags, { Name = "${local.name_prefix}-rds-sg" })
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_app" {
  security_group_id            = aws_security_group.rds.id
  description                  = "PostgreSQL from the application security group"
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = var.db_port
  to_port                      = var.db_port
}

resource "aws_security_group" "redis" {
  name        = "${local.name_prefix}-redis-sg"
  description = "ElastiCache Redis: 6379 from the application security group only"
  vpc_id      = var.vpc_id

  egress = []

  tags = merge(var.tags, { Name = "${local.name_prefix}-redis-sg" })
}

resource "aws_vpc_security_group_ingress_rule" "redis_from_app" {
  security_group_id            = aws_security_group.redis.id
  description                  = "Redis from the application security group"
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = var.redis_port
  to_port                      = var.redis_port
}

# --- IAM ---------------------------------------------------------------------

data "aws_iam_policy_document" "ecs_tasks_assume_role" {
  statement {
    sid     = "EcsTasksAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_execution" {
  name               = "${local.name_prefix}-ecs-execution"
  description        = "ECS agent role: pull images, write logs, resolve injected secrets and parameters"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume_role.json

  tags = merge(var.tags, { Name = "${local.name_prefix}-ecs-execution" })
}

data "aws_iam_policy_document" "ecs_execution" {
  statement {
    sid     = "EcrAuthorizationToken"
    effect  = "Allow"
    actions = ["ecr:GetAuthorizationToken"]

    # ecr:GetAuthorizationToken does not support resource-level permissions.
    # This is the only "*" resource for a pull-side action in the configuration.
    resources = ["*"]
  }

  statement {
    sid    = "EcrPull"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = local.ecr_repository_arns
  }

  statement {
    sid    = "WriteContainerLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = local.log_group_arn_patterns
  }

  statement {
    sid       = "ReadInjectedSecrets"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = var.secret_arns
  }

  statement {
    sid    = "ReadInjectedParameters"
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
    ]
    resources = var.ssm_parameter_arns
  }

  dynamic "statement" {
    for_each = length(var.kms_key_arns) > 0 ? [1] : []

    content {
      sid       = "DecryptCustomerManagedKeys"
      effect    = "Allow"
      actions   = ["kms:Decrypt"]
      resources = var.kms_key_arns
    }
  }
}

resource "aws_iam_role_policy" "ecs_execution" {
  name   = "${local.name_prefix}-ecs-execution"
  role   = aws_iam_role.ecs_execution.id
  policy = data.aws_iam_policy_document.ecs_execution.json
}

resource "aws_iam_role" "ecs_task" {
  name               = "${local.name_prefix}-ecs-task"
  description        = "Running application role: own secrets, own bucket prefix, traces"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume_role.json

  tags = merge(var.tags, { Name = "${local.name_prefix}-ecs-task" })
}

data "aws_iam_policy_document" "ecs_task" {
  statement {
    sid       = "ReadOwnSecrets"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = var.secret_arns
  }

  statement {
    sid    = "ReadWriteOwnDocumentPrefix"
    effect = "Allow"
    actions = [
      "s3:AbortMultipartUpload",
      "s3:DeleteObject",
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = var.documents_bucket_arn == "" ? [] : ["${var.documents_bucket_arn}/${var.documents_prefix}/*"]
  }

  statement {
    sid       = "ListOwnDocumentPrefix"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = var.documents_bucket_arn == "" ? [] : [var.documents_bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${var.documents_prefix}/*"]
    }
  }

  statement {
    sid    = "WriteCollectorLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = local.log_group_arn_patterns
  }

  dynamic "statement" {
    for_each = var.enable_xray ? [1] : []

    content {
      sid    = "PublishTraces"
      effect = "Allow"
      actions = [
        "xray:PutTelemetryRecords",
        "xray:PutTraceSegments",
      ]

      # X-Ray write actions do not support resource-level permissions.
      resources = ["*"]
    }
  }
}

resource "aws_iam_role_policy" "ecs_task" {
  name   = "${local.name_prefix}-ecs-task"
  role   = aws_iam_role.ecs_task.id
  policy = data.aws_iam_policy_document.ecs_task.json
}
