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

variable "enable_cloudfront" {
  description = "Create the CloudFront distribution. Off in dev (the ALB is addressed directly); on in prod."
  type        = bool
  default     = false
}

variable "alb_dns_name" {
  description = "DNS name of the ALB, used as the /api origin."
  type        = string
}

variable "alb_origin_https" {
  description = "Whether the ALB serves HTTPS. When true CloudFront talks to the origin over TLS only."
  type        = bool
  default     = false
}

variable "web_bucket_id" {
  description = "ID of the web assets bucket. CloudFront reads it through an origin access control."
  type        = string
}

variable "web_bucket_arn" {
  description = "ARN of the web assets bucket."
  type        = string
}

variable "web_bucket_regional_domain_name" {
  description = "Regional domain name of the web assets bucket."
  type        = string
}

variable "price_class" {
  description = "CloudFront price class. PriceClass_100 is the cheapest and covers North America and Europe."
  type        = string
  default     = "PriceClass_100"

  validation {
    condition     = contains(["PriceClass_All", "PriceClass_200", "PriceClass_100"], var.price_class)
    error_message = "price_class must be one of: PriceClass_All, PriceClass_200, PriceClass_100."
  }
}

variable "aliases" {
  description = "Alternate domain names. Must be empty when no ACM certificate is supplied."
  type        = list(string)
  default     = []
}

variable "acm_certificate_arn" {
  description = "ACM certificate ARN for the alternate domain names. Must be in us-east-1 for CloudFront."
  type        = string
  default     = ""
}

variable "web_acl_id" {
  description = "Optional AWS WAF web ACL ARN to attach at the edge. Empty attaches nothing."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Tags merged onto every taggable resource in this module."
  type        = map(string)
  default     = {}
}
