output "cluster_id" {
  description = "ECS cluster ID."
  value       = aws_ecs_cluster.this.id
}

output "cluster_name" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.this.name
}

output "cluster_arn" {
  description = "ECS cluster ARN."
  value       = aws_ecs_cluster.this.arn
}

output "alb_id" {
  description = "Application Load Balancer ID."
  value       = aws_lb.this.id
}

output "alb_arn" {
  description = "Application Load Balancer ARN."
  value       = aws_lb.this.arn
}

output "alb_arn_suffix" {
  description = "ALB ARN suffix, used as the CloudWatch metric dimension."
  value       = aws_lb.this.arn_suffix
}

output "alb_dns_name" {
  description = "Public DNS name of the load balancer. The CloudFront origin in prod."
  value       = aws_lb.this.dns_name
}

output "alb_zone_id" {
  description = "Route 53 zone ID of the load balancer, for an alias record."
  value       = aws_lb.this.zone_id
}

output "api_target_group_arn" {
  description = "API target group ARN."
  value       = aws_lb_target_group.api.arn
}

output "api_target_group_arn_suffix" {
  description = "API target group ARN suffix, used as a CloudWatch metric dimension."
  value       = aws_lb_target_group.api.arn_suffix
}

output "api_target_group_name" {
  description = "API target group name."
  value       = aws_lb_target_group.api.name
}

output "web_target_group_arn" {
  description = "Web target group ARN."
  value       = aws_lb_target_group.web.arn
}

output "web_target_group_arn_suffix" {
  description = "Web target group ARN suffix."
  value       = aws_lb_target_group.web.arn_suffix
}

output "api_service_name" {
  description = "API ECS service name."
  value       = aws_ecs_service.api.name
}

output "web_service_name" {
  description = "Web ECS service name."
  value       = aws_ecs_service.web.name
}

output "api_task_definition_arn" {
  description = "ARN of the current API task definition."
  value       = aws_ecs_task_definition.api.arn
}

output "web_task_definition_arn" {
  description = "ARN of the current web task definition."
  value       = aws_ecs_task_definition.web.arn
}

output "migrate_task_definition_arn" {
  description = "ARN of the one-off migration task definition the pipeline runs before the service update."
  value       = aws_ecs_task_definition.migrate.arn
}

output "migrate_task_definition_family" {
  description = "Family of the one-off migration task definition."
  value       = aws_ecs_task_definition.migrate.family
}

output "api_ecr_repository_url" {
  description = "ECR repository URL for the API image."
  value       = aws_ecr_repository.api.repository_url
}

output "web_ecr_repository_url" {
  description = "ECR repository URL for the web image."
  value       = aws_ecr_repository.web.repository_url
}

output "api_ecr_repository_arn" {
  description = "ECR repository ARN for the API image."
  value       = aws_ecr_repository.api.arn
}

output "web_ecr_repository_arn" {
  description = "ECR repository ARN for the web image."
  value       = aws_ecr_repository.web.arn
}

output "log_group_names" {
  description = "Map of service name to CloudWatch log group name."
  value       = { for name, group in aws_cloudwatch_log_group.service : name => group.name }
}

output "log_group_arns" {
  description = "Map of service name to CloudWatch log group ARN."
  value       = { for name, group in aws_cloudwatch_log_group.service : name => group.arn }
}

output "api_appautoscaling_target_resource_id" {
  description = "Resource ID of the API autoscaling target."
  value       = aws_appautoscaling_target.api.resource_id
}
