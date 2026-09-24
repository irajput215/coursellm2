output "replication_group_id" {
  description = "ElastiCache replication group ID."
  value       = aws_elasticache_replication_group.this.id
}

output "primary_endpoint_address" {
  description = "Primary endpoint hostname. Private, resolvable only inside the VPC."
  value       = aws_elasticache_replication_group.this.primary_endpoint_address
}

output "reader_endpoint_address" {
  description = "Reader endpoint hostname."
  value       = aws_elasticache_replication_group.this.reader_endpoint_address
}

output "port" {
  description = "Redis port."
  value       = aws_elasticache_replication_group.this.port
}

output "transit_encryption_enabled" {
  description = "Whether TLS and AUTH are required to connect."
  value       = var.transit_encryption_enabled
}

output "redis_url_secret_arn" {
  description = "Secrets Manager ARN holding the assembled REDIS_URL."
  value       = aws_secretsmanager_secret.redis_url.arn
}

output "redis_url_secret_name" {
  description = "Name of the Redis URL secret."
  value       = aws_secretsmanager_secret.redis_url.name
}
