# dev environment inputs. Every variable is typed and described; the defaults
# are the smallest defensible sizing (docs/architecture/deployment.md section 1
# and section 11).

variable "project" {
  description = "Project name, used as the first component of every resource name and tag."
  type        = string
  default     = "coursellm"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.project))
    error_message = "project must be a short lowercase name suitable for AWS resource names."
  }
}

variable "environment" {
  description = "Environment name. Pinned to dev in this root module."
  type        = string
  default     = "dev"

  validation {
    condition     = var.environment == "dev"
    error_message = "This root module is the dev environment; environment must be \"dev\". Use ../prod for prod."
  }
}

variable "aws_region" {
  description = "AWS region. Also determines which prices apply; the cost table in README.md assumes us-east-1."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]$", var.aws_region))
    error_message = "aws_region must look like us-east-1."
  }
}

variable "availability_zones" {
  description = "Availability zones to spread the two public and two private subnets across."
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]

  validation {
    condition     = length(var.availability_zones) >= 2
    error_message = "At least two availability zones are required for an ALB and for RDS Multi-AZ."
  }
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.0.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "vpc_cidr must be a valid CIDR block."
  }
}

variable "alb_ingress_cidrs" {
  description = "CIDRs allowed to reach the public ALB. The ALB is the only component that accepts internet ingress."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "alb_certificate_arn" {
  description = "ACM certificate ARN for an HTTPS ALB listener. Empty leaves dev on HTTP; see README.md."
  type        = string
  default     = ""
}

# -- storage ----------------------------------------------------------------

variable "documents_versioning_enabled" {
  description = "Keep every version of a document object."
  type        = bool
  default     = false
}

variable "documents_expiration_days" {
  description = "Expire current document objects after this many days. 0 keeps them."
  type        = number
  default     = 0
}

variable "documents_transition_days" {
  description = "Transition document objects to STANDARD_IA after this many days. 0 disables the transition."
  type        = number
  default     = 0
}

variable "logs_expiration_days" {
  description = "Expire S3 access-log objects after this many days."
  type        = number
  default     = 30
}

variable "documents_prefix" {
  description = "Key prefix inside the documents bucket the task role may read and write."
  type        = string
  default     = "tenants"
}

# -- database ---------------------------------------------------------------

variable "db_instance_class" {
  description = "RDS instance class for dev."
  type        = string
  default     = "db.t4g.micro"

  validation {
    condition     = can(regex("^db\\.[a-z0-9]+\\.[a-z0-9]+$", var.db_instance_class))
    error_message = "db_instance_class must look like db.t4g.micro."
  }
}

variable "db_engine_version" {
  description = "PostgreSQL engine version. RDS PostgreSQL 16 ships pgvector on every 16.x minor."
  type        = string
  default     = "16"

  validation {
    condition     = can(regex("^16(\\.[0-9]+)?$", var.db_engine_version))
    error_message = "db_engine_version must be 16 or a 16.x minor."
  }
}

variable "db_allocated_storage" {
  description = "RDS storage in GiB."
  type        = number
  default     = 20

  validation {
    condition     = var.db_allocated_storage >= 20
    error_message = "RDS PostgreSQL requires at least 20 GiB."
  }
}

variable "db_multi_az" {
  description = "Run a synchronous standby. Off in dev."
  type        = bool
  default     = false
}

variable "db_backup_retention_period" {
  description = "Automated backup retention in days. One day in dev."
  type        = number
  default     = 1

  validation {
    condition     = var.db_backup_retention_period >= 0 && var.db_backup_retention_period <= 35
    error_message = "db_backup_retention_period must be between 0 and 35."
  }
}

variable "db_deletion_protection" {
  description = "Block deletion of the RDS instance. Off in dev so an environment can be torn down."
  type        = bool
  default     = false
}

variable "db_skip_final_snapshot" {
  description = "Skip the final snapshot when the instance is destroyed. True in dev."
  type        = bool
  default     = true
}

variable "db_performance_insights_enabled" {
  description = "Enable Performance Insights (free 7-day retention)."
  type        = bool
  default     = true
}

# -- cache ------------------------------------------------------------------

variable "cache_node_type" {
  description = "ElastiCache node type for dev."
  type        = string
  default     = "cache.t4g.micro"

  validation {
    condition     = can(regex("^cache\\.[a-z0-9]+\\.[a-z0-9]+$", var.cache_node_type))
    error_message = "cache_node_type must look like cache.t4g.micro."
  }
}

variable "cache_num_cache_clusters" {
  description = "Number of cache nodes. One in dev: no replica, no failover."
  type        = number
  default     = 1

  validation {
    condition     = var.cache_num_cache_clusters >= 1 && var.cache_num_cache_clusters <= 6
    error_message = "cache_num_cache_clusters must be between 1 and 6."
  }
}

# -- compute ----------------------------------------------------------------

variable "api_image_tag" {
  description = "Immutable API image tag, normally the git SHA. The pipeline supplies this."
  type        = string
  default     = "bootstrap"
}

variable "web_image_tag" {
  description = "Immutable web image tag, normally the git SHA."
  type        = string
  default     = "bootstrap"
}

variable "api_cpu" {
  description = "Task-level CPU units for the API task. 512 (0.5 vCPU) because the ADOT sidecar shares the task allocation."
  type        = number
  default     = 512

  validation {
    condition     = contains([256, 512, 1024, 2048, 4096], var.api_cpu)
    error_message = "api_cpu must be a valid Fargate CPU value."
  }
}

variable "api_memory" {
  description = "Task-level memory in MiB for the API task."
  type        = number
  default     = 1024
}

variable "api_desired_count" {
  description = "Initial API task count."
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
  default     = 2
}

variable "web_cpu" {
  description = "Task-level CPU units for the web task."
  type        = number
  default     = 256
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

variable "migrate_cpu" {
  description = "CPU units for the one-off migration task."
  type        = number
  default     = 512
}

variable "migrate_memory" {
  description = "Memory in MiB for the one-off migration task."
  type        = number
  default     = 1024
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention in days."
  type        = number
  default     = 14

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.log_retention_days)
    error_message = "log_retention_days must be a value CloudWatch Logs accepts."
  }
}

# -- observability ----------------------------------------------------------

variable "alarm_email" {
  description = "Email subscribed to the alarm topic. Empty creates the topic with no subscriber."
  type        = string
  default     = ""
}

variable "enable_billing_alarm" {
  description = "Create the EstimatedCharges alarm. Only receives data in us-east-1."
  type        = bool
  default     = false
}

# -- edge -------------------------------------------------------------------

variable "enable_cloudfront" {
  description = "Create a CloudFront distribution. Off in dev; the ALB is addressed directly."
  type        = bool
  default     = false
}

variable "cloudfront_acm_certificate_arn" {
  description = "ACM certificate ARN for CloudFront alternate domain names. Must be in us-east-1."
  type        = string
  default     = ""
}

variable "cloudfront_aliases" {
  description = "CloudFront alternate domain names. Must be empty without a certificate."
  type        = list(string)
  default     = []
}

variable "cors_allowed_origins" {
  description = "Value of CORS_ALLOWED_ORIGINS for the API."
  type        = string
  default     = "http://localhost:5173"
}
