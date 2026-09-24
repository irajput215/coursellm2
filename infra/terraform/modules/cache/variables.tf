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

variable "private_subnet_ids" {
  description = "Private subnet IDs for the ElastiCache subnet group."
  type        = list(string)

  validation {
    condition     = length(var.private_subnet_ids) >= 2
    error_message = "The cache subnet group should span at least two subnets."
  }
}

variable "security_group_ids" {
  description = "Security groups attached to the replication group. This is the redis-sg, which accepts 6379 from the app group only."
  type        = list(string)
}

variable "node_type" {
  description = "ElastiCache node type. The smallest defensible default is cache.t4g.micro."
  type        = string
  default     = "cache.t4g.micro"

  validation {
    condition     = can(regex("^cache\\.[a-z0-9]+\\.[a-z0-9]+$", var.node_type))
    error_message = "node_type must look like an ElastiCache node type, for example cache.t4g.micro."
  }
}

variable "engine_version" {
  description = "Redis engine version."
  type        = string
  default     = "7.1"

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+$", var.engine_version))
    error_message = "engine_version must look like 7.1."
  }
}

variable "parameter_group_name" {
  description = "Cache parameter group. Must match the engine major version, for example default.redis7."
  type        = string
  default     = "default.redis7"
}

variable "num_cache_clusters" {
  description = "Number of cache nodes. 1 in dev; 2 gives a primary and a replica and enables automatic failover."
  type        = number
  default     = 1

  validation {
    condition     = var.num_cache_clusters >= 1 && var.num_cache_clusters <= 6
    error_message = "num_cache_clusters must be between 1 and 6."
  }
}

variable "automatic_failover_enabled" {
  description = "Promote a replica on primary failure. Requires at least two cache clusters."
  type        = bool
  default     = false
}

variable "multi_az_enabled" {
  description = "Place the replica in a different AZ. Requires automatic_failover_enabled."
  type        = bool
  default     = false
}

variable "transit_encryption_enabled" {
  description = "TLS in transit plus an AUTH token. On in both environments; the cache is private either way."
  type        = bool
  default     = true
}

variable "snapshot_retention_limit" {
  description = "Days of Redis snapshots to retain. 0 disables snapshots (dev)."
  type        = number
  default     = 0

  validation {
    condition     = var.snapshot_retention_limit >= 0 && var.snapshot_retention_limit <= 35
    error_message = "snapshot_retention_limit must be between 0 and 35 days."
  }
}

variable "snapshot_window" {
  description = "Preferred snapshot window in UTC when snapshot_retention_limit is greater than 0."
  type        = string
  default     = "05:00-06:00"
}

variable "maintenance_window" {
  description = "Preferred maintenance window in UTC."
  type        = string
  default     = "sun:06:00-sun:07:00"
}

variable "kms_key_arn" {
  description = "Customer-managed KMS key for at-rest encryption. Empty uses the AWS-managed service key."
  type        = string
  default     = ""
}

variable "secret_recovery_window_days" {
  description = "Secrets Manager recovery window for the generated cache URL."
  type        = number
  default     = 7

  validation {
    condition     = var.secret_recovery_window_days == 0 || (var.secret_recovery_window_days >= 7 && var.secret_recovery_window_days <= 30)
    error_message = "secret_recovery_window_days must be 0 or between 7 and 30."
  }
}

variable "tags" {
  description = "Tags merged onto every taggable resource in this module."
  type        = map(string)
  default     = {}
}
