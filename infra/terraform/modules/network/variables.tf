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
  description = "AWS region the VPC and its endpoints live in."
  type        = string

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]$", var.aws_region))
    error_message = "aws_region must look like an AWS region id, for example us-east-1."
  }
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.0.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "vpc_cidr must be a valid IPv4 CIDR block, for example 10.0.0.0/16."
  }
}

variable "availability_zones" {
  description = "Availability zones to spread subnets across. Two is the minimum an ALB and RDS Multi-AZ accept."
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]

  validation {
    condition     = length(var.availability_zones) >= 2
    error_message = "At least two availability zones are required."
  }
}

variable "public_subnet_cidrs" {
  description = "CIDRs for the public subnets, one per availability zone. Empty derives /20s from vpc_cidr."
  type        = list(string)
  default     = []
}

variable "private_subnet_cidrs" {
  description = "CIDRs for the private subnets, one per availability zone. Empty derives /20s from vpc_cidr."
  type        = list(string)
  default     = []
}

variable "single_nat_gateway" {
  description = "Share one NAT gateway across every private subnet (cheaper, one AZ of egress failure) instead of one per AZ."
  type        = bool
  default     = true
}

variable "enable_vpc_endpoints" {
  description = "Create the S3 gateway endpoint and the interface endpoints listed in interface_endpoints."
  type        = bool
  default     = true
}

variable "interface_endpoints" {
  description = "Interface VPC endpoint service short names. Keeps AWS API traffic off NAT and lets tasks pull images without a NAT dependency. Each interface endpoint is billed per endpoint-hour plus per GB, so the default is the four deployment.md section 6 names for; add more only with the cost in mind."
  type        = list(string)
  default = [
    "ecr.api",
    "ecr.dkr",
    "secretsmanager",
    "logs",
  ]
}

variable "tags" {
  description = "Tags merged onto every taggable resource in this module. Provider default_tags supplies Project, Environment and ManagedBy."
  type        = map(string)
  default     = {}
}
