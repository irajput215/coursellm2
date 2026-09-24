variable "project" {
  description = "Project name, used as the first component of every resource name."
  type        = string
  default     = "coursellm"
}

variable "environment" {
  description = "Environment name. One of dev or prod."
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be one of: dev, prod."
  }
}

variable "aws_region" {
  description = "AWS region the alarms and dashboard are created in."
  type        = string
}

variable "alb_arn_suffix" {
  description = "ALB ARN suffix, the LoadBalancer metric dimension."
  type        = string
}

variable "api_target_group_arn_suffix" {
  description = "API target group ARN suffix, the TargetGroup metric dimension."
  type        = string
}

variable "web_target_group_arn_suffix" {
  description = "Web target group ARN suffix."
  type        = string
}

variable "ecs_cluster_name" {
  description = "ECS cluster name, the ClusterName metric dimension."
  type        = string
}

variable "api_service_name" {
  description = "API ECS service name, the ServiceName metric dimension."
  type        = string
}

variable "web_service_name" {
  description = "Web ECS service name."
  type        = string
}

variable "rds_instance_id" {
  description = "RDS instance identifier, the DBInstanceIdentifier metric dimension."
  type        = string
}

variable "redis_replication_group_id" {
  description = "ElastiCache replication group ID, the ReplicationGroupId metric dimension."
  type        = string
}

variable "alarm_email" {
  description = "Email address subscribed to the alarm topic. Empty creates the topic with no subscriber; an unconfirmed subscription receives nothing."
  type        = string
  default     = ""
}

variable "alb_5xx_rate_threshold_percent" {
  description = "5xx (target plus ELB) as a percentage of requests before the alarm fires. security/observability specs page at 2%."
  type        = number
  default     = 2
}

variable "target_response_time_p99_threshold_seconds" {
  description = "p99 target response time before the alarm fires. The interactive budget for a tutor answer is 8s."
  type        = number
  default     = 8
}

variable "unhealthy_host_threshold" {
  description = "Unhealthy host count at which the alarm fires."
  type        = number
  default     = 1
}

variable "rds_cpu_threshold_percent" {
  description = "RDS CPU utilisation percentage at which the alarm fires."
  type        = number
  default     = 80
}

variable "rds_connections_threshold" {
  description = "RDS connection count at which the alarm fires. Compare with db_pool_size x task count."
  type        = number
  default     = 80
}

variable "rds_free_storage_threshold_bytes" {
  description = "Free storage in bytes below which the alarm fires. Default is 2 GiB."
  type        = number
  default     = 2147483648
}

variable "redis_cpu_threshold_percent" {
  description = "ElastiCache CPU utilisation percentage at which the alarm fires."
  type        = number
  default     = 75
}

variable "redis_memory_threshold_percent" {
  description = "ElastiCache memory usage percentage at which the alarm fires."
  type        = number
  default     = 80
}

variable "enable_billing_alarm" {
  description = "Create the EstimatedCharges alarm. It only receives data in us-east-1, so leave it off when the provider region is elsewhere."
  type        = bool
  default     = false
}

variable "billing_alarm_threshold_usd" {
  description = "Estimated month-to-date charges in USD at which the billing alarm fires."
  type        = number
  default     = 100
}

variable "adot_config" {
  description = "ADOT collector configuration YAML. Empty uses the module default, which exports OTLP traces to X-Ray and metrics as CloudWatch EMF."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Tags merged onto every taggable resource in this module."
  type        = map(string)
  default     = {}
}
