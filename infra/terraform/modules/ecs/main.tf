# ECS layer, part 1: cluster, capacity providers, image repositories and log
# groups.
#
# The service runs on Fargate, not EKS. docs/architecture/deployment.md section
# 5 records the trade-off: this workload is three stateless HTTP services, and a
# Kubernetes control plane would add an upgrade cadence and an ingress layer for
# no capability the system uses.
#
# Nothing is applied. See README.md.

locals {
  name_prefix = "${var.project}-${var.environment}"

  service_names = toset(["api", "web", "migrate", "adot"])

  api_container_environment = merge(
    {
      ENVIRONMENT                 = var.environment
      JWT_ALGORITHM               = "HS256"
      OTEL_ENABLED                = "true"
      OTEL_EXPORTER_OTLP_ENDPOINT = "http://127.0.0.1:4318"
      OTEL_SERVICE_NAME           = "coursellm-api"
      PYTHONUNBUFFERED            = "1"
    },
    var.api_environment,
  )

  web_container_environment = merge(
    {
      ENVIRONMENT = var.environment
    },
    var.web_environment,
  )

  # ECS resolves both Secrets Manager secrets and SSM parameters through the
  # `secrets` block. Non-secret configuration is therefore injected by ARN too,
  # which keeps the plaintext values out of the task definition.
  api_container_secrets = merge(var.api_secret_values, var.api_ssm_values)

  api_environment_list = [
    for name in sort(keys(local.api_container_environment)) : {
      name  = name
      value = local.api_container_environment[name]
    }
  ]

  web_environment_list = [
    for name in sort(keys(local.web_container_environment)) : {
      name  = name
      value = local.web_container_environment[name]
    }
  ]

  api_secrets_list = [
    for name in sort(keys(local.api_container_secrets)) : {
      name      = name
      valueFrom = local.api_container_secrets[name]
    }
  ]

  migrate_secrets_list = [
    for name in sort(keys(var.migrate_secret_values)) : {
      name      = name
      valueFrom = var.migrate_secret_values[name]
    }
  ]

  # The migration container overrides the API image's uvicorn entrypoint. The
  # `$OWNER_DATABASE_URL` and `$APP_ROLE_PASSWORD` values arrive from Secrets
  # Manager through the `secrets` block, so no literal appears here.
  #
  # Order matters: migrate first, then bootstrap. Alembic creates the tables, so
  # the grants in scripts/bootstrap_db.sql cover them immediately, and the
  # auth_login_lookup function exists by the time the script tries to tighten its
  # EXECUTE grants. Default privileges cover tables a later migration adds.
  migrate_script = join("; ", [
    "set -euo pipefail",
    "alembic upgrade head",
    "psql \"$OWNER_DATABASE_URL\" -v ON_ERROR_STOP=1 -f /app/scripts/bootstrap_db.sql",
    "psql \"$OWNER_DATABASE_URL\" -v ON_ERROR_STOP=1 -v app_pw=\"$APP_ROLE_PASSWORD\" -c \"ALTER ROLE ${var.app_role_name} WITH LOGIN PASSWORD :'app_pw'\"",
  ])
}

resource "aws_ecs_cluster" "this" {
  name = "${local.name_prefix}-cluster"

  setting {
    name  = "containerInsights"
    value = var.container_insights
  }

  tags = merge(var.tags, { Name = "${local.name_prefix}-cluster" })
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name = aws_ecs_cluster.this.name

  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    base              = 1
    weight            = 1
  }
}

# --- image repositories ------------------------------------------------------

# Images are tagged with the git SHA and the repositories are IMMUTABLE, so a
# task definition names a fixed artefact and a rollback points at the same bytes
# that ran before. `latest` may exist for convenience but is never referenced.
resource "aws_ecr_repository" "api" {
  name                 = "${local.name_prefix}-api"
  image_tag_mutability = var.ecr_image_tag_mutability
  force_delete         = var.ecr_force_delete

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = merge(var.tags, { Name = "${local.name_prefix}-api" })
}

resource "aws_ecr_repository" "web" {
  name                 = "${local.name_prefix}-web"
  image_tag_mutability = var.ecr_image_tag_mutability
  force_delete         = var.ecr_force_delete

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = merge(var.tags, { Name = "${local.name_prefix}-web" })
}

resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 14 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 14
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep the most recent 30 tagged images"
        selection = {
          tagStatus      = "tagged"
          tagPatternList = ["*"]
          countType      = "imageCountMoreThan"
          countNumber    = 30
        }
        action = { type = "expire" }
      },
    ]
  })
}

resource "aws_ecr_lifecycle_policy" "web" {
  repository = aws_ecr_repository.web.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 14 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 14
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep the most recent 30 tagged images"
        selection = {
          tagStatus      = "tagged"
          tagPatternList = ["*"]
          countType      = "imageCountMoreThan"
          countNumber    = 30
        }
        action = { type = "expire" }
      },
    ]
  })
}

# --- log groups --------------------------------------------------------------

resource "aws_cloudwatch_log_group" "service" {
  for_each = local.service_names

  name              = "/ecs/${local.name_prefix}/${each.value}"
  retention_in_days = var.log_retention_days

  tags = merge(var.tags, { Name = "${local.name_prefix}-${each.value}-logs" })
}
