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
  description = "AWS region, used to build IAM resource ARNs."
  type        = string

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]$", var.aws_region))
    error_message = "aws_region must look like an AWS region id, for example us-east-1."
  }
}

variable "vpc_id" {
  description = "VPC the security groups are created in."
  type        = string
}

variable "alb_ingress_cidrs" {
  description = "CIDRs allowed to reach the public ALB on 80/443. The load balancer is the only internet-facing component."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "app_port" {
  description = "Port the API container listens on."
  type        = number
  default     = 8000
}

variable "web_port" {
  description = "Port the web container listens on."
  type        = number
  default     = 8080
}

variable "db_port" {
  description = "PostgreSQL port."
  type        = number
  default     = 5432
}

variable "redis_port" {
  description = "Redis port."
  type        = number
  default     = 6379
}

variable "secret_arns" {
  description = "Secrets Manager secret ARNs the ECS execution and task roles may read. Never a wildcard."
  type        = list(string)
  default     = []
}

variable "ssm_parameter_arns" {
  description = "SSM Parameter Store parameter ARNs the ECS execution role may read for non-secret configuration."
  type        = list(string)
  default     = []
}

variable "documents_bucket_arn" {
  description = "ARN of the documents bucket. The task role can read and write only the prefix below."
  type        = string
  default     = ""
}

variable "documents_prefix" {
  description = "Key prefix inside the documents bucket the task role may read and write."
  type        = string
  default     = "tenants"
}

variable "kms_key_arns" {
  description = "Customer-managed KMS key ARNs the task role may decrypt with. Empty uses the AWS-managed service keys."
  type        = list(string)
  default     = []
}

variable "enable_xray" {
  description = "Grant the task role the X-Ray write actions the ADOT collector uses."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags merged onto every taggable resource in this module."
  type        = map(string)
  default     = {}
}
