# CourseLLM prod environment.
#
# A separate root module with separate state from dev. `var.environment` is
# pinned to "prod" by a validation rule so a stray `-var` cannot point this
# state at dev resources or the other way round.
#
# Nothing here is applied. `terraform plan` is the only command intended, and a
# plan creates nothing. See ../README.md for the architecture, the cost drivers
# with their assumptions, and the explicit NOT-APPLIED statement.

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
  # ManagedBy.
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

  # Non-secret configuration. Keys must match fields on
  # coursellm.core.config.Settings; values go to SSM Parameter Store and are
  # injected into the task by ARN.
  config_parameters = {
    LOG_LEVEL                     = "INFO"
    DB_POOL_SIZE                  = "10"
    CACHE_ENABLED                 = "true"
    RERANK_ENABLED                = "true"
    RETRIEVAL_TOP_K_PER_RETRIEVER = "20"
    RRF_K                         = "60"
    RERANK_TOP_K                  = "5"
    CONTEXT_TOKEN_BUDGET          = "3000"
    EMBEDDING_MODEL               = "BAAI/bge-small-en-v1.5"
    MAX_OUTPUT_TOKENS             = "1024"
    MAX_QUERY_CHARS               = "2000"
    OTEL_SAMPLE_RATIO             = var.otel_sample_ratio
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

  # Prod wants one NAT gateway per AZ so a single AZ failure does not remove
  # egress for the surviving tasks. That choice multiplies the NAT hourly line
  # by the number of AZs; README.md states the consequence.
  single_nat_gateway = false

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

  documents_versioning_enabled = true
  documents_expiration_days    = var.documents_expiration_days
  documents_transition_days    = var.documents_transition_days
  logs_expiration_days         = var.logs_expiration_days

  # A destroy must not be able to delete a non-empty bucket of real documents.
  force_destroy = false
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

  # Prod secrets keep a recovery window: deleting one is reversible for 30 days.
  recovery_window_days = 30
}

# ---------------------------------------------------------------------------
# Database and cache
# ---------------------------------------------------------------------------
module "database" {
  source = "../../modules/database"

  project     = var.project
  environment = var.environment
  tags        = local.tags

  private_subnet_ids = module.network.private_subnet_ids
  security_group_ids = [module.security.rds_security_group_id]

  instance_class = var.db_instance_class
  engine_version = var.db_engine_version

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_max_allocated_storage
  multi_az              = var.db_multi_az

  backup_retention_period = var.db_backup_retention_period

  deletion_protection = true
  skip_final_snapshot = false

  performance_insights_enabled = true

  secret_recovery_window_days = 30
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
  automatic_failover_enabled  = true
  multi_az_enabled            = true
  snapshot_retention_limit    = var.cache_snapshot_retention_limit
  secret_recovery_window_days = 30
}

# ---------------------------------------------------------------------------
# Security groups and IAM
# ---------------------------------------------------------------------------
module "security" {
  source = "../../modules/security"

  project     = var.project
  environment = var.environment
  aws_region  = var.aws_region
  vpc_id      = module.network.vpc_id
  tags        = local.tags

  # The ALB is the only component with an internet ingress rule. A CloudFront
  # managed prefix list would narrow this further; see README.md "Hardening not
  # done here".
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
# Observability
# ---------------------------------------------------------------------------
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

  # Requires the provider region to be us-east-1 for the metric to exist.
  enable_billing_alarm        = var.aws_region == "us-east-1"
  billing_alarm_threshold_usd = var.billing_alarm_threshold_usd

  rds_connections_threshold = var.rds_connections_threshold
}

# ---------------------------------------------------------------------------
# ECS
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
  certificate_arn    = var.alb_certificate_arn

  alb_deletion_protection = true
  container_insights      = "enabled"

  api_ssm_values = module.secrets.config_parameter_arns

  api_secret_values = {
    DATABASE_URL      = "${module.database.app_secret_arn}:DATABASE_URL::"
    REDIS_URL         = "${module.cache.redis_url_secret_arn}:REDIS_URL::"
    SECRET_KEY        = module.secrets.jwt_secret_arn
    OPENAI_API_KEY    = module.secrets.provider_secret_arns["OPENAI_API_KEY"]
    ANTHROPIC_API_KEY = module.secrets.provider_secret_arns["ANTHROPIC_API_KEY"]
    LANGSMITH_API_KEY = module.secrets.provider_secret_arns["LANGSMITH_API_KEY"]
  }

  # The one-off migration task connects as the schema owner and runs
  # scripts/bootstrap_db.sql so coursellm_app exists with
  # NOSUPERUSER NOBYPASSRLS; the API service connects only as coursellm_app.
  migrate_secret_values = {
    OWNER_DATABASE_URL = "${module.database.master_secret_arn}:OWNER_DATABASE_URL::"
    APP_ROLE_PASSWORD  = "${module.database.app_secret_arn}:password::"
  }
}

# ---------------------------------------------------------------------------
# Edge (CloudFront)
# ---------------------------------------------------------------------------
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

  price_class         = var.cloudfront_price_class
  acm_certificate_arn = var.cloudfront_acm_certificate_arn
  aliases             = var.cloudfront_aliases
}
