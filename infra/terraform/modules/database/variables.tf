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
  description = "Private subnet IDs for the DB subnet group. RDS is never given a public subnet."
  type        = list(string)

  validation {
    condition     = length(var.private_subnet_ids) >= 2
    error_message = "RDS requires a subnet group spanning at least two subnets for Multi-AZ."
  }
}

variable "security_group_ids" {
  description = "Security groups attached to the instance. This is the rds-sg, which accepts 5432 from the app group only."
  type        = list(string)
}

variable "instance_class" {
  description = "RDS instance class. The smallest defensible default is db.t4g.micro."
  type        = string
  default     = "db.t4g.micro"

  validation {
    condition     = can(regex("^db\\.[a-z0-9]+\\.[a-z0-9]+$", var.instance_class))
    error_message = "instance_class must look like an RDS instance class, for example db.t4g.micro."
  }
}

variable "engine_version" {
  description = <<-EOT
    PostgreSQL engine version. "16" selects the current RDS PostgreSQL 16 minor
    release. Every RDS PostgreSQL 16 minor ships the pgvector extension (the
    extension table lists pgvector from 16.1 onward), and the migration runs
    CREATE EXTENSION IF NOT EXISTS vector, so a major-version pin is safe.
  EOT
  type        = string
  default     = "16"

  validation {
    condition     = can(regex("^16(\\.[0-9]+)?$", var.engine_version))
    error_message = "engine_version must be 16 or a 16.x minor release; pgvector support is verified for the 16 line."
  }
}

variable "parameter_group_family" {
  description = "DB parameter group family. Must match engine_version: postgres16 for PostgreSQL 16."
  type        = string
  default     = "postgres16"

  validation {
    condition     = can(regex("^postgres1[0-9]$", var.parameter_group_family))
    error_message = "parameter_group_family must look like postgres16."
  }
}

variable "db_name" {
  description = "Initial database name."
  type        = string
  default     = "coursellm"
}

variable "master_username" {
  description = "Master (schema owner) user name. Owns the schema and runs migrations and scripts/bootstrap_db.sql."
  type        = string
  default     = "coursellm_owner"

  validation {
    condition     = can(regex("^[a-z_][a-z0-9_]{0,62}$", var.master_username))
    error_message = "master_username must be a lowercase PostgreSQL identifier."
  }
}

variable "app_role_name" {
  description = "Application role created by scripts/bootstrap_db.sql with NOSUPERUSER NOBYPASSRLS. The API task connects as this role."
  type        = string
  default     = "coursellm_app"

  validation {
    condition     = can(regex("^[a-z_][a-z0-9_]{0,62}$", var.app_role_name))
    error_message = "app_role_name must be a lowercase PostgreSQL identifier."
  }
}

variable "allocated_storage" {
  description = "Storage in GiB. gp3, encrypted at rest."
  type        = number
  default     = 20

  validation {
    condition     = var.allocated_storage >= 20
    error_message = "RDS PostgreSQL requires at least 20 GiB."
  }
}

variable "max_allocated_storage" {
  description = "Upper bound for storage autoscaling in GiB. 0 disables autoscaling."
  type        = number
  default     = 0
}

variable "storage_type" {
  description = "RDS storage type. gp3 is the current general-purpose default."
  type        = string
  default     = "gp3"

  validation {
    condition     = contains(["gp3", "gp2", "io1", "io2"], var.storage_type)
    error_message = "storage_type must be one of: gp3, gp2, io1, io2."
  }
}

variable "multi_az" {
  description = "Run a synchronous standby in a second AZ. Off in dev; on in prod."
  type        = bool
  default     = false
}

variable "backup_retention_period" {
  description = "Automated backup retention in days. 0 disables backups; prod must be greater than 0."
  type        = number
  default     = 1

  validation {
    condition     = var.backup_retention_period >= 0 && var.backup_retention_period <= 35
    error_message = "backup_retention_period must be between 0 and 35 days."
  }
}

variable "backup_window" {
  description = "Preferred automated backup window in UTC."
  type        = string
  default     = "03:00-04:00"
}

variable "maintenance_window" {
  description = "Preferred maintenance window in UTC."
  type        = string
  default     = "sun:04:30-sun:05:30"
}

variable "deletion_protection" {
  description = "Block deletion of the instance. On in prod."
  type        = bool
  default     = false
}

variable "skip_final_snapshot" {
  description = "Skip the final snapshot on destroy. Must be false in prod."
  type        = bool
  default     = true
}

variable "final_snapshot_identifier" {
  description = "Name for the final snapshot when skip_final_snapshot is false."
  type        = string
  default     = "coursellm-final"
}

variable "performance_insights_enabled" {
  description = "Enable Performance Insights. The 7-day retention tier is free."
  type        = bool
  default     = true
}

variable "performance_insights_retention_period" {
  description = "Performance Insights retention in days. 7 is the free tier."
  type        = number
  default     = 7

  validation {
    condition     = contains([7, 731], var.performance_insights_retention_period)
    error_message = "performance_insights_retention_period must be 7 (free tier) or 731 (paid)."
  }
}

variable "auto_minor_version_upgrade" {
  description = "Apply minor engine upgrades automatically during the maintenance window."
  type        = bool
  default     = true
}

variable "enabled_cloudwatch_logs_exports" {
  description = "PostgreSQL log types exported to CloudWatch Logs."
  type        = list(string)
  default     = ["postgresql", "upgrade"]

  validation {
    condition     = alltrue([for log in var.enabled_cloudwatch_logs_exports : contains(["postgresql", "upgrade", "iam-auth", "pg_audit"], log)])
    error_message = "enabled_cloudwatch_logs_exports may contain only postgresql, upgrade, iam-auth and pg_audit."
  }
}

variable "kms_key_arn" {
  description = "Customer-managed KMS key for storage encryption. Empty uses the AWS-managed aws/rds key."
  type        = string
  default     = ""
}

variable "secret_recovery_window_days" {
  description = "Secrets Manager recovery window for the generated database credentials. 0 forces immediate deletion."
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
