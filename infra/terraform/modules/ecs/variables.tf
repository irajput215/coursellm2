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
  description = "AWS region, written into the awslogs configuration and the ADOT container."
  type        = string
}

variable "vpc_id" {
  description = "VPC the ALB and target groups live in."
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnets for the internet-facing ALB."
  type        = list(string)

  validation {
    condition     = length(var.public_subnet_ids) >= 2
    error_message = "An Application Load Balancer requires at least two subnets in different AZs."
  }
}

variable "private_subnet_ids" {
  description = "Private subnets the ECS tasks run in."
  type        = list(string)

  validation {
    condition     = length(var.private_subnet_ids) >= 2
    error_message = "Tasks should be spread across at least two private subnets."
  }
}

variable "alb_security_group_id" {
  description = "Security group for the ALB."
  type        = string
}

variable "app_security_group_id" {
  description = "Security group for the API and migration tasks."
  type        = string
}

variable "web_security_group_id" {
  description = "Security group for the web tasks."
  type        = string
}

variable "execution_role_arn" {
  description = "ECS task execution role ARN. Pulls images, writes logs, resolves injected secrets and parameters."
  type        = string
}

variable "task_role_arn" {
  description = "ECS task role ARN the running application assumes."
  type        = string
}

variable "app_role_name" {
  description = "Application database role name, used by the one-off migration task when it sets the role password."
  type        = string
  default     = "coursellm_app"
}

variable "api_image_tag" {
  description = "Immutable image tag for the API image, normally the git SHA. `latest` is never used in a task definition."
  type        = string
  default     = "bootstrap"
}

variable "web_image_tag" {
  description = "Immutable image tag for the web image, normally the git SHA."
  type        = string
  default     = "bootstrap"
}

variable "adot_image" {
  description = "AWS Distro for OpenTelemetry collector image for the API sidecar."
  type        = string
  default     = "public.ecr.aws/aws-observability/aws-otel-collector:v0.40.0"
}

variable "adot_config_parameter_arn" {
  description = "SSM parameter ARN holding the ADOT collector configuration, injected as AOT_CONFIG_CONTENT."
  type        = string
}

variable "cpu_architecture" {
  description = "Fargate CPU architecture. Must match how the images were built."
  type        = string
  default     = "X86_64"

  validation {
    condition     = contains(["X86_64", "ARM64"], var.cpu_architecture)
    error_message = "cpu_architecture must be X86_64 or ARM64."
  }
}

variable "api_cpu" {
  description = "Task-level CPU units for the API task (1024 = 1 vCPU). The ADOT sidecar shares this."
  type        = number
  default     = 512

  validation {
    condition     = contains([256, 512, 1024, 2048, 4096, 8192, 16384], var.api_cpu)
    error_message = "api_cpu must be a valid Fargate CPU value."
  }
}

variable "api_memory" {
  description = "Task-level memory in MiB for the API task."
  type        = number
  default     = 1024

  validation {
    condition     = var.api_memory >= 512 && var.api_memory <= 122880
    error_message = "api_memory must be between 512 and 122880 MiB."
  }
}

variable "api_desired_count" {
  description = "Initial API task count. Autoscaling owns this after the first apply."
  type        = number
  default     = 1
}

variable "api_min_capacity" {
  description = "Minimum API tasks under autoscaling."
  type        = number
  default     = 1
}

variable "api_max_capacity" {
  description = "Maximum API tasks under autoscaling."
  type        = number
  default     = 4
}

variable "api_request_count_target" {
  description = "Target ALB RequestCountPerTarget for the API. Request count is the primary signal; CPU lags on an I/O-bound async service."
  type        = number
  default     = 600
}

variable "api_cpu_target" {
  description = "Target average CPU utilisation percentage for the API."
  type        = number
  default     = 65
}

variable "web_cpu" {
  description = "Task-level CPU units for the web task."
  type        = number
  default     = 256

  validation {
    condition     = contains([256, 512, 1024, 2048, 4096], var.web_cpu)
    error_message = "web_cpu must be a valid Fargate CPU value."
  }
}

variable "web_memory" {
  description = "Task-level memory in MiB for the web task."
  type        = number
  default     = 512
}

variable "web_desired_count" {
  description = "Initial web task count."
  type        = number
  default     = 1
}

variable "web_min_capacity" {
  description = "Minimum web tasks under autoscaling."
  type        = number
  default     = 1
}

variable "web_max_capacity" {
  description = "Maximum web tasks under autoscaling."
  type        = number
  default     = 2
}

variable "web_cpu_target" {
  description = "Target average CPU utilisation percentage for the web service."
  type        = number
  default     = 65
}

variable "migrate_cpu" {
  description = "Task-level CPU units for the one-off migration task."
  type        = number
  default     = 512
}

variable "migrate_memory" {
  description = "Task-level memory in MiB for the one-off migration task."
  type        = number
  default     = 1024
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention. A log group with no retention keeps (and bills for) every line forever."
  type        = number
  default     = 30

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.log_retention_days)
    error_message = "log_retention_days must be one of the values CloudWatch Logs accepts."
  }
}

variable "container_insights" {
  description = "ECS container insights level: disabled, enabled or enhanced. Enhanced adds per-task metrics and cost."
  type        = string
  default     = "enabled"

  validation {
    condition     = contains(["disabled", "enabled", "enhanced"], var.container_insights)
    error_message = "container_insights must be one of: disabled, enabled, enhanced."
  }
}

variable "certificate_arn" {
  description = "ACM certificate ARN for the HTTPS listener. Empty keeps the ALB on plain HTTP, which is only acceptable when CloudFront terminates TLS."
  type        = string
  default     = ""
}

variable "api_health_check_path" {
  description = "ALB health check path for the API target group. Readiness, not liveness."
  type        = string
  default     = "/readyz"
}

variable "web_health_check_path" {
  description = "ALB health check path for the web target group. nginx serves /healthz, its liveness endpoint."
  type        = string
  default     = "/healthz"
}

variable "deregistration_delay_seconds" {
  description = "Target group deregistration delay. Must exceed the longest streamed SSE response so an answer is not cut mid-token."
  type        = number
  default     = 120
}

variable "alb_idle_timeout_seconds" {
  description = "ALB idle timeout. Raised above the default 60s for long-lived SSE streams."
  type        = number
  default     = 300
}

variable "alb_deletion_protection" {
  description = "Block deletion of the load balancer. On in prod."
  type        = bool
  default     = false
}

variable "health_check_grace_period_seconds" {
  description = "Seconds ECS ignores failing ALB health checks after a task starts."
  type        = number
  default     = 60
}

variable "api_environment" {
  description = "Non-secret environment variables for the API container."
  type        = map(string)
  default     = {}
}

variable "web_environment" {
  description = "Non-secret environment variables for the web container."
  type        = map(string)
  default     = {}
}

variable "api_secret_values" {
  description = "Map of environment variable name to Secrets Manager valueFrom for the API container. Values are ARNs (optionally with a :json-key:: suffix), never literal secrets."
  type        = map(string)
  default     = {}
}

variable "api_ssm_values" {
  description = "Map of environment variable name to SSM parameter ARN for non-secret API configuration. ECS resolves these through the same `secrets` block."
  type        = map(string)
  default     = {}
}

variable "migrate_secret_values" {
  description = "Map of environment variable name to Secrets Manager valueFrom for the one-off migration container."
  type        = map(string)
  default     = {}
}

variable "ecr_image_tag_mutability" {
  description = "ECR tag mutability. IMMUTABLE means a pushed tag can never be overwritten, which is what makes a rollback name a fixed artefact."
  type        = string
  default     = "IMMUTABLE"

  validation {
    condition     = contains(["MUTABLE", "IMMUTABLE"], var.ecr_image_tag_mutability)
    error_message = "ecr_image_tag_mutability must be MUTABLE or IMMUTABLE."
  }
}

variable "ecr_force_delete" {
  description = "Allow ECR repositories to be deleted even when they contain images."
  type        = bool
  default     = false
}

variable "enable_execute_command" {
  description = "Allow ECS Exec into running tasks. Off by default: it is an interactive path into a container holding tenant data."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags merged onto every taggable resource in this module."
  type        = map(string)
  default     = {}
}
