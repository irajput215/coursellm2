# Canonical Terraform and provider constraints for the CourseLLM AWS estate.
#
# Terraform reads only the `.tf` files in the directory it is invoked in, so each
# environment root module (`environments/dev`, `environments/prod`) carries an
# identical `terraform` block in its `main.tf`. This file is the canonical copy
# that documents the pin; keep the two in step when either is bumped.
#
#   - Terraform >= 1.5.0 so that `import` blocks, `check` blocks and the
#     improved `moved` semantics are all available, and nothing in this
#     configuration uses syntax newer than that.
#   - AWS provider >= 5.60, < 6.0. It is the first minor line in which every
#     resource used here (`aws_vpc_security_group_ingress_rule`,
#     `aws_s3_bucket_*` sub-resources, `aws_ecs_cluster_capacity_providers`,
#     `aws_cloudfront_origin_access_control`) is stable. Provider 6.x removed
#     arguments this configuration uses, so the upper bound is deliberate.
#   - `random` for generated database, cache and signing credentials.
#
# Nothing in this configuration is applied. See README.md.

terraform {
  required_version = ">= 1.5.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }

    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}
