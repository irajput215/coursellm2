# Cache layer: ElastiCache for Redis in the private subnets.
#
# Scope is deliberately narrow. The cache holds retrieval and embedding results
# and rate-limit counters (docs/architecture/deployment.md section 4.1). It is
# not durable state: losing it degrades latency and fails rate limiting open
# (docs/architecture/security.md section 11), it does not lose documents.
#
# Encryption at rest is always on. Transit encryption (and therefore the AUTH
# token) is on by default; local compose runs plain Redis, which is acceptable
# because local Redis is not reachable from anywhere.
#
# Nothing is applied. See README.md.

locals {
  name_prefix      = "${var.project}-${var.environment}"
  auth_token       = var.transit_encryption_enabled ? random_password.auth_token.result : null
  scheme           = var.transit_encryption_enabled ? "rediss" : "redis"
  primary_endpoint = aws_elasticache_replication_group.this.primary_endpoint_address
  redis_url        = "${local.scheme}://:${urlencode(var.transit_encryption_enabled ? random_password.auth_token.result : "")}@${local.primary_endpoint}:6379/0"
}

resource "random_password" "auth_token" {
  length  = 32
  special = false
}

resource "aws_elasticache_subnet_group" "this" {
  name        = "${local.name_prefix}-redis-subnets"
  description = "Private subnets for ${local.name_prefix} Redis"
  subnet_ids  = var.private_subnet_ids

  tags = merge(var.tags, { Name = "${local.name_prefix}-redis-subnets" })
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = "${local.name_prefix}-redis"
  description          = "${local.name_prefix} retrieval and rate-limit cache"

  engine               = "redis"
  engine_version       = var.engine_version
  parameter_group_name = var.parameter_group_name
  node_type            = var.node_type
  port                 = 6379

  num_cache_clusters         = var.num_cache_clusters
  automatic_failover_enabled = var.automatic_failover_enabled
  multi_az_enabled           = var.multi_az_enabled

  at_rest_encryption_enabled = true
  kms_key_id                 = var.kms_key_arn == "" ? null : var.kms_key_arn
  transit_encryption_enabled = var.transit_encryption_enabled
  auth_token                 = local.auth_token

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = var.security_group_ids

  snapshot_retention_limit = var.snapshot_retention_limit
  snapshot_window          = var.snapshot_retention_limit > 0 ? var.snapshot_window : null
  maintenance_window       = var.maintenance_window

  auto_minor_version_upgrade = true
  apply_immediately          = false

  tags = merge(var.tags, { Name = "${local.name_prefix}-redis" })
}

# The URL is assembled here and stored in Secrets Manager, exactly as the
# database URL is: the task definition references the secret ARN and never
# carries the value in its environment block.
resource "aws_secretsmanager_secret" "redis_url" {
  name        = "${local.name_prefix}/redis/url"
  description = "ElastiCache ${local.scheme}:// URL (with AUTH token) for ${local.name_prefix}."
  kms_key_id  = var.kms_key_arn == "" ? null : var.kms_key_arn

  recovery_window_in_days = var.secret_recovery_window_days

  tags = merge(var.tags, { Name = "${local.name_prefix}/redis/url" })
}

resource "aws_secretsmanager_secret_version" "redis_url" {
  secret_id = aws_secretsmanager_secret.redis_url.id

  secret_string = jsonencode({
    host        = local.primary_endpoint
    port        = 6379
    REDIS_URL   = local.redis_url
    tls_enabled = var.transit_encryption_enabled
  })
}
