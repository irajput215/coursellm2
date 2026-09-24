# CourseLLM dev environment.
#
# This root module is the dev environment and nothing else: `var.environment`
# is pinned to "dev" by a validation rule, so a stray `-var` cannot point the
# dev state at production resources. Prod is a separate root with its own state
# (see ../prod and backend.tf).
#
# Nothing here is applied. `terraform plan` is the only command that has been
# run against this configuration, and a plan creates nothing.
#
# See ../README.md for the architecture, the cost drivers and the explicit
# NOT-APPLIED statement.

terraform {
  required_version = ">= 1.5.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }

    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "aws" {
  region = var.aws_region

  # default_tags is what makes a cost report possible: every taggable resource
  # created by this root, in any module, carries Project, Environment and
  # ManagedBy without each module having to remember them.
  default_tags {
    tags = local.tags
  }
}

locals {
  name_prefix = "${var.project}-${var.environment}"

  tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  # Non-secret configuration. These go to SSM Parameter Store and are injected
  # into the task by ARN; they are not secrets and do not belong in Secrets
  # Manager. Keys must match fields on coursellm.core.config.Settings.
  config_parameters = {
    LOG_LEVEL                     = "INFO"
    DB_POOL_SIZE                  = "5"
    CACHE_ENABLED                 = "true"
    RERANK_ENABLED                = "true"
    RETRIEVAL_TOP_K_PER_RETRIEVER = "20"
    RRF_K                         = "60"
    RERANK_TOP_K                  = "5"
    CONTEXT_TOKEN_BUDGET          = "3000"
    EMBEDDING_MODEL               = "BAAI/bge-small-en-v1.5"
    MAX_OUTPUT_TOKENS             = "1024"
    MAX_QUERY_CHARS               = "2000"
    OTEL_SAMPLE_RATIO             = "1.0"
    LANGSMITH_ENABLED             = "false"
    CORS_ALLOWED_ORIGINS          = var.cors_allowed_origins
  }
}

# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------
module "network" {
  source = "../../modules/network"

  project            = var.project
  environment        = var.environment
  aws_region         = var.aws_region
  vpc_cidr           = var.vpc_cidr
  availability_zones = var.availability_zones

  # One NAT gateway. It is the single largest fixed line in a low-traffic dev
  # account, and dev does not need per-AZ egress survival.
  single_nat_gateway = true

  tags = local.tags
}

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
module "storage" {
  source = "../../modules/storage"

  project     = var.project
  environment = var.environment
  tags        = local.tags

  documents_versioning_enabled = var.documents_versioning_enabled
  documents_expiration_days    = var.documents_expiration_days
  documents_transition_days    = var.documents_transition_days
  logs_expiration_days         = var.logs_expiration_days

  # Dev data is synthetic; an operator may empty the bucket by hand. Prod keeps
  # this false so a destroy cannot silently discard real student data.
  force_destroy = true
}

# ---------------------------------------------------------------------------
# Application secrets and non-secret configuration
# ---------------------------------------------------------------------------
module "secrets" {
  source = "../../modules/secrets"

  project     = var.project
  environment = var.environment
  tags        = local.tags

  config_parameters = local.config_parameters

  # Dev secrets can be destroyed immediately; there is nothing to recover.
  recovery_window_days = 0
}

# ---------------------------------------------------------------------------
# Database and cache
# ---------------------------------------------------------------------------
# The database module owns the generated master password and the generated
# application-role password, and writes both to Secrets Manager. The cache
# module owns the generated Redis AUTH token the same way. No password is
# written to a file.
module "database" {
  source = "../../modules/database"

  project     = var.project
  environment = var.environment
  tags        = local.tags

  private_subnet_ids = module.network.private_subnet_ids
  security_group_ids = [module.security.rds_security_group_id]

  instance_class = var.db_instance_class
  engine_version = var.db_engine_version

  allocated_storage = var.db_allocated_storage
  multi_az          = var.db_multi_az

  backup_retention_period = var.db_backup_retention_period

  deletion_protection = var.db_deletion_protection
  skip_final_snapshot = var.db_skip_final_snapshot

  performance_insights_enabled = var.db_performance_insights_enabled

  secret_recovery_window_days = 0
}

module "cache" {
  source = "../../modules/cache"

  project     = var.project
  environment = var.environment
  tags        = local.tags

  private_subnet_ids = module.network.private_subnet_ids
  security_group_ids = [module.security.redis_security_group_id]

  node_type                   = var.cache_node_type
  num_cache_clusters          = var.cache_num_cache_clusters
  automatic_failover_enabled  = false
  multi_az_enabled            = false
  snapshot_retention_limit    = 0
  secret_recovery_window_days = 0
}

# ---------------------------------------------------------------------------
# Security groups and IAM
# ---------------------------------------------------------------------------
# This module consumes secret ARNs from the modules above. Terraform's graph is
# per-resource, so the IAM policy depends on the secret resources (which do not
# depend on the database instance), while the database instance depends on the
# rds security group (which does not depend on IAM). There is no cycle.
module "security" {
  source = "../../modules/security"

  project     = var.project
  environment = var.environment
  aws_region  = var.aws_region
  vpc_id      = module.network.vpc_id
  tags        = local.tags

  # The ALB is the only component with 0.0.0.0/0 ingress.
  alb_ingress_cidrs = var.alb_ingress_cidrs

  secret_arns = concat(
    module.secrets.all_secret_arns,
    [
      module.database.master_secret_arn,
      module.database.app_secret_arn,
      module.cache.redis_url_secret_arn,
    ],
  )

  ssm_parameter_arns = concat(
    values(module.secrets.config_parameter_arns),
    [module.observability.adot_config_parameter_arn],
  )

  documents_bucket_arn = module.storage.documents_bucket_arn
  documents_prefix     = var.documents_prefix
}

# ---------------------------------------------------------------------------
# Observability: alarms, dashboard and the ADOT collector configuration
# ---------------------------------------------------------------------------
# The ADOT configuration parameter does not depend on any ECS-derived input, so
# referencing it from the ECS module below does not create a dependency cycle.
module "observability" {
  source = "../../modules/observability"

  project     = var.project
  environment = var.environment
  aws_region  = var.aws_region
  tags        = local.tags

  alb_arn_suffix              = module.ecs.alb_arn_suffix
  api_target_group_arn_suffix = module.ecs.api_target_group_arn_suffix
  web_target_group_arn_suffix = module.ecs.web_target_group_arn_suffix
  ecs_cluster_name            = module.ecs.cluster_name
  api_service_name            = module.ecs.api_service_name
  web_service_name            = module.ecs.web_service_name

  rds_instance_id            = module.database.db_instance_id
  redis_replication_group_id = module.cache.replication_group_id

  alarm_email = var.alarm_email

  # AWS/Billing EstimatedCharges is published only in us-east-1. dev runs in
  # us-east-1, so the alarm can be enabled; it is off to avoid noise on a
  # development account.
  enable_billing_alarm = var.enable_billing_alarm
}

# ---------------------------------------------------------------------------
# ECS: cluster, task definitions, services, ALB
# ---------------------------------------------------------------------------
module "ecs" {
  source = "../../modules/ecs"

  project     = var.project
  environment = var.environment
  aws_region  = var.aws_region
  tags        = local.tags

  vpc_id                    = module.network.vpc_id
  public_subnet_ids         = module.network.public_subnet_ids
  private_subnet_ids        = module.network.private_subnet_ids
  alb_security_group_id     = module.security.alb_security_group_id
  app_security_group_id     = module.security.app_security_group_id
  web_security_group_id     = module.security.web_security_group_id
  execution_role_arn        = module.security.ecs_execution_role_arn
  task_role_arn             = module.security.ecs_task_role_arn
  app_role_name             = module.database.app_role_name
  adot_config_parameter_arn = module.observability.adot_config_parameter_arn

  api_image_tag = var.api_image_tag
  web_image_tag = var.web_image_tag

  api_cpu           = var.api_cpu
  api_memory        = var.api_memory
  api_desired_count = var.api_desired_count
  api_min_capacity  = var.api_min_capacity
  api_max_capacity  = var.api_max_capacity
  web_cpu           = var.web_cpu
  web_memory        = var.web_memory
  web_desired_count = var.web_desired_count
  web_min_capacity  = var.web_min_capacity
  web_max_capacity  = var.web_max_capacity

  migrate_cpu    = var.migrate_cpu
  migrate_memory = var.migrate_memory

  log_retention_days = var.log_retention_days

  # dev has no ACM certificate; the ALB is reached over HTTP. Prod supplies a
  # certificate and HTTP is redirected to HTTPS.
  certificate_arn = var.alb_certificate_arn

  # Non-secret API configuration from SSM, injected by ARN.
  api_ssm_values = module.secrets.config_parameter_arns

  # Credentials from Secrets Manager, injected by ARN. DATABASE_URL is assembled
  # inside the secret rather than stored as a plaintext environment value.
  api_secret_values = {
    DATABASE_URL      = "${module.database.app_secret_arn}:DATABASE_URL::"
    REDIS_URL         = "${module.cache.redis_url_secret_arn}:REDIS_URL::"
    SECRET_KEY        = module.secrets.jwt_secret_arn
    OPENAI_API_KEY    = module.secrets.provider_secret_arns["OPENAI_API_KEY"]
    ANTHROPIC_API_KEY = module.secrets.provider_secret_arns["ANTHROPIC_API_KEY"]
    LANGSMITH_API_KEY = module.secrets.provider_secret_arns["LANGSMITH_API_KEY"]
  }

  # The one-off migration task connects as the schema owner and runs
  # scripts/bootstrap_db.sql, which creates coursellm_app with
  # NOSUPERUSER NOBYPASSRLS. It never connects as the application role.
  migrate_secret_values = {
    OWNER_DATABASE_URL = "${module.database.master_secret_arn}:OWNER_DATABASE_URL::"
    APP_ROLE_PASSWORD  = "${module.database.app_secret_arn}:password::"
  }
}

# ---------------------------------------------------------------------------
# Edge (CloudFront)
# ---------------------------------------------------------------------------
# dev addresses the ALB directly: no CloudFront, no distribution cost.
module "edge" {
  source = "../../modules/edge"

  project     = var.project
  environment = var.environment
  tags        = local.tags

  enable_cloudfront = var.enable_cloudfront

  alb_dns_name                    = module.ecs.alb_dns_name
  alb_origin_https                = var.alb_certificate_arn != ""
  web_bucket_id                   = module.storage.web_bucket_id
  web_bucket_arn                  = module.storage.web_bucket_arn
  web_bucket_regional_domain_name = module.storage.web_bucket_regional_domain_name

  acm_certificate_arn = var.cloudfront_acm_certificate_arn
  aliases             = var.cloudfront_aliases
}
