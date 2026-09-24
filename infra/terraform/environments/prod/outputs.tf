# prod environment outputs. These reference resources the configuration would
# create on apply; none of them exists today. Nothing here is a claim that a
# service is running or a bill is being generated.

output "environment" {
  description = "Environment name."
  value       = var.environment
}

output "aws_region" {
  description = "Region everything would be created in."
  value       = var.aws_region
}

output "vpc_id" {
  description = "VPC ID."
  value       = module.network.vpc_id
}

output "public_subnet_ids" {
  description = "Public subnet IDs (ALB and NAT)."
  value       = module.network.public_subnet_ids
}

output "private_subnet_ids" {
  description = "Private subnet IDs (ECS tasks, RDS, ElastiCache)."
  value       = module.network.private_subnet_ids
}

output "nat_gateway_count" {
  description = "Number of NAT gateways. One per AZ in prod, which multiplies the NAT hourly line."
  value       = module.network.nat_gateway_count
}

output "alb_dns_name" {
  description = "Public DNS name of the load balancer (the CloudFront origin)."
  value       = module.ecs.alb_dns_name
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution ID, or null when disabled."
  value       = module.edge.distribution_id
}

output "cloudfront_domain_name" {
  description = "CloudFront distribution domain name. Set CORS_ALLOWED_ORIGINS and the web build's API base to this."
  value       = module.edge.distribution_domain_name
}

output "ecs_cluster_name" {
  description = "ECS cluster name."
  value       = module.ecs.cluster_name
}

output "api_service_name" {
  description = "API ECS service name."
  value       = module.ecs.api_service_name
}

output "web_service_name" {
  description = "Web ECS service name."
  value       = module.ecs.web_service_name
}

output "migrate_task_definition_family" {
  description = "Family of the one-off migration task definition the pipeline runs before the service update."
  value       = module.ecs.migrate_task_definition_family
}

output "migrate_task_definition_arn" {
  description = "ARN of the one-off migration task definition."
  value       = module.ecs.migrate_task_definition_arn
}

output "api_ecr_repository_url" {
  description = "ECR repository URL for the API image."
  value       = module.ecs.api_ecr_repository_url
}

output "web_ecr_repository_url" {
  description = "ECR repository URL for the web image."
  value       = module.ecs.web_ecr_repository_url
}

output "db_instance_id" {
  description = "RDS instance identifier."
  value       = module.database.db_instance_id
}

output "db_address" {
  description = "RDS hostname (private)."
  value       = module.database.db_address
  sensitive   = true
}

output "db_app_secret_name" {
  description = "Name of the Secrets Manager secret holding the application role DATABASE_URL."
  value       = module.database.app_secret_name
}

output "db_master_secret_name" {
  description = "Name of the Secrets Manager secret holding the schema-owner credentials."
  value       = module.database.master_secret_name
}

output "redis_endpoint" {
  description = "ElastiCache primary endpoint."
  value       = module.cache.primary_endpoint_address
  sensitive   = true
}

output "documents_bucket_name" {
  description = "Documents bucket name."
  value       = module.storage.bucket_names["documents"]
}

output "web_bucket_name" {
  description = "Web assets bucket name."
  value       = module.storage.bucket_names["web"]
}

output "logs_bucket_name" {
  description = "Logs bucket name."
  value       = module.storage.bucket_names["logs"]
}

output "alarm_topic_arn" {
  description = "SNS topic the CloudWatch alarms notify."
  value       = module.observability.alarm_topic_arn
}

output "alarm_names" {
  description = "CloudWatch alarms that would be created."
  value       = module.observability.alarm_names
}

output "dashboard_name" {
  description = "CloudWatch dashboard name."
  value       = module.observability.dashboard_name
}

output "dashboard_url" {
  description = "Console URL of the dashboard. It does not exist until the configuration is applied."
  value       = module.observability.dashboard_url
}

output "adot_config_parameter_name" {
  description = "SSM parameter holding the ADOT collector configuration."
  value       = module.observability.adot_config_parameter_name
}
