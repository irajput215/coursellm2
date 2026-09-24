output "db_instance_id" {
  description = "RDS instance identifier."
  value       = aws_db_instance.this.id
}

output "db_instance_arn" {
  description = "RDS instance ARN."
  value       = aws_db_instance.this.arn
}

output "db_address" {
  description = "RDS hostname. Private, resolvable only inside the VPC."
  value       = aws_db_instance.this.address
}

output "db_port" {
  description = "PostgreSQL port."
  value       = aws_db_instance.this.port
}

output "db_name" {
  description = "Initial database name."
  value       = aws_db_instance.this.db_name
}

output "engine_version" {
  description = "Engine version actually configured on the instance."
  value       = aws_db_instance.this.engine_version
}

output "parameter_group_name" {
  description = "Custom parameter group name (postgres16 family, rds.force_ssl = 1)."
  value       = aws_db_parameter_group.this.name
}

output "master_secret_arn" {
  description = "Secrets Manager ARN holding the schema-owner credentials and OWNER_DATABASE_URL."
  value       = aws_secretsmanager_secret.master.arn
}

output "master_secret_name" {
  description = "Name of the schema-owner secret."
  value       = aws_secretsmanager_secret.master.name
}

output "app_secret_arn" {
  description = "Secrets Manager ARN holding the application role credentials and the assembled DATABASE_URL."
  value       = aws_secretsmanager_secret.app.arn
}

output "app_secret_name" {
  description = "Name of the application secret."
  value       = aws_secretsmanager_secret.app.name
}

output "app_role_name" {
  description = "Application role name the API task connects as."
  value       = var.app_role_name
}

output "master_username" {
  description = "Schema owner / master user name."
  value       = var.master_username
}
