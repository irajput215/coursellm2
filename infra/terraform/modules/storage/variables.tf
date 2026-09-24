variable "project" {
  description = "Project name, used as the first component of every bucket name."
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

variable "documents_versioning_enabled" {
  description = "Keep every version of a document object. On in prod."
  type        = bool
  default     = false
}

variable "web_versioning_enabled" {
  description = "Keep every version of a web asset. Useful for rolling a bundle back."
  type        = bool
  default     = false
}

variable "documents_expiration_days" {
  description = "Expire current document objects after this many days. 0 keeps them."
  type        = number
  default     = 0
}

variable "documents_transition_days" {
  description = "Move document objects to STANDARD_IA after this many days. 0 disables the transition."
  type        = number
  default     = 0
}

variable "logs_expiration_days" {
  description = "Expire access-log objects after this many days. A log bucket with no expiry bills forever."
  type        = number
  default     = 90
}

variable "noncurrent_version_expiration_days" {
  description = "Expire noncurrent object versions after this many days in versioned buckets."
  type        = number
  default     = 30
}

variable "enable_access_logging" {
  description = "Write S3 server access logs for the documents and web buckets into the logs bucket."
  type        = bool
  default     = true
}

variable "force_destroy" {
  description = "Allow a destroy to delete a non-empty bucket. Must stay false so data is not discarded by a destroy."
  type        = bool
  default     = false
}

variable "kms_key_arn" {
  description = "Customer-managed KMS key for bucket encryption. Empty uses SSE-S3 (AES256), which is also encrypted at rest."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Tags merged onto every taggable resource in this module."
  type        = map(string)
  default     = {}
}
