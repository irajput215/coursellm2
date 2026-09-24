output "alb_security_group_id" {
  description = "Security group attached to the public ALB."
  value       = aws_security_group.alb.id
}

output "app_security_group_id" {
  description = "Security group for the API and migration tasks. RDS and Redis accept traffic from this group only."
  value       = aws_security_group.app.id
}

output "web_security_group_id" {
  description = "Security group for the web tasks."
  value       = aws_security_group.web.id
}

output "rds_security_group_id" {
  description = "Security group for RDS. Ingress is 5432 from the app group; there is no CIDR ingress."
  value       = aws_security_group.rds.id
}

output "redis_security_group_id" {
  description = "Security group for ElastiCache. Ingress is 6379 from the app group; there is no CIDR ingress."
  value       = aws_security_group.redis.id
}

output "ecs_execution_role_arn" {
  description = "ARN of the ECS task execution role."
  value       = aws_iam_role.ecs_execution.arn
}

output "ecs_execution_role_name" {
  description = "Name of the ECS task execution role."
  value       = aws_iam_role.ecs_execution.name
}

output "ecs_task_role_arn" {
  description = "ARN of the ECS task role the running application assumes."
  value       = aws_iam_role.ecs_task.arn
}

output "ecs_task_role_name" {
  description = "Name of the ECS task role."
  value       = aws_iam_role.ecs_task.name
}
