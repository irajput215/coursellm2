# Storage layer: three S3 buckets with the same baseline controls.
#
#   documents  raw uploads and extracted artefacts  (the application's object store)
#   web        the built React client, served through CloudFront (see modules/edge)
#   logs       S3 server access logs and exports
#
# Baseline controls on each bucket:
#   - public access blocked, ACLs disabled (BucketOwnerEnforced)
#   - encryption at rest (SSE-S3 by default; a customer-managed KMS key can be passed in)
#   - TLS required by bucket policy
#   - lifecycle rules that abort incomplete multipart uploads and expire objects
#     explicitly, because a bucket with no lifecycle rule bills forever
#   - versioning where the environment asks for it
#
# The web bucket's policy is owned by modules/edge, because only the CloudFront
# distribution knows its own ARN and the bucket must name it in the policy.
#
# Nothing is applied. See README.md.

locals {
  name_prefix = "${var.project}-${var.environment}"

  buckets = {
    documents = {
      purpose         = "Course documents: raw uploads and extracted artefacts"
      versioning      = var.documents_versioning_enabled
      expiration_days = var.documents_expiration_days
      transition_days = var.documents_transition_days
    }
    web = {
      purpose         = "Built React client served by CloudFront"
      versioning      = var.web_versioning_enabled
      expiration_days = 0
      transition_days = 0
    }
    logs = {
      purpose         = "S3 server access logs and exports"
      versioning      = false
      expiration_days = var.logs_expiration_days
      transition_days = 0
    }
  }

  # modules/edge owns the web bucket policy so it can name the distribution ARN.
  policy_buckets = { for name, config in local.buckets : name => config if name != "web" }

  logs_bucket_arn = aws_s3_bucket.this["logs"].arn
}

data "aws_caller_identity" "current" {}

# Bucket names are globally unique, so a random suffix is added once and then
# kept stable in state.
resource "random_id" "bucket_suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "this" {
  for_each = local.buckets

  bucket        = "${local.name_prefix}-${each.key}-${random_id.bucket_suffix.hex}"
  force_destroy = var.force_destroy

  tags = merge(var.tags, {
    Name    = "${local.name_prefix}-${each.key}"
    Purpose = each.value.purpose
  })
}

resource "aws_s3_bucket_ownership_controls" "this" {
  for_each = local.buckets

  bucket = aws_s3_bucket.this[each.key].id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each = local.buckets

  bucket = aws_s3_bucket.this[each.key].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = local.buckets

  bucket = aws_s3_bucket.this[each.key].id

  versioning_configuration {
    status = each.value.versioning ? "Enabled" : "Suspended"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = local.buckets

  bucket = aws_s3_bucket.this[each.key].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = var.kms_key_arn == "" ? "AES256" : "aws:kms"
      kms_master_key_id = var.kms_key_arn == "" ? null : var.kms_key_arn
    }

    # A bucket key cuts KMS request cost substantially when a customer-managed
    # key is in use.
    bucket_key_enabled = var.kms_key_arn != ""
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  for_each = local.buckets

  bucket = aws_s3_bucket.this[each.key].id

  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  dynamic "rule" {
    for_each = each.value.expiration_days > 0 ? [1] : []

    content {
      id     = "expire-current-objects"
      status = "Enabled"

      filter {}

      expiration {
        days = each.value.expiration_days
      }
    }
  }

  dynamic "rule" {
    for_each = each.value.transition_days > 0 ? [1] : []

    content {
      id     = "transition-to-standard-ia"
      status = "Enabled"

      filter {}

      transition {
        days          = each.value.transition_days
        storage_class = "STANDARD_IA"
      }
    }
  }

  dynamic "rule" {
    for_each = each.value.versioning ? [1] : []

    content {
      id     = "expire-noncurrent-versions"
      status = "Enabled"

      filter {}

      noncurrent_version_expiration {
        noncurrent_days = var.noncurrent_version_expiration_days
      }
    }
  }
}

# TLS-only access. Bucket default encryption covers encryption at rest, so an
# upload that omits the x-amz-server-side-encryption header is still encrypted;
# denying on a missing header would break clients that rely on the bucket
# default, which is why this policy does not do that.
data "aws_iam_policy_document" "bucket" {
  for_each = local.policy_buckets

  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions = ["s3:*"]

    resources = [
      aws_s3_bucket.this[each.key].arn,
      "${aws_s3_bucket.this[each.key].arn}/*",
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  # S3 server access logging delivers into the logs bucket through the
  # logging.s3.amazonaws.com service principal, so the logs bucket must allow
  # it. BucketOwnerEnforced disables ACLs, which is why this is a policy
  # statement rather than the legacy log-delivery ACL.
  #
  # It is a statement in this document, not a second aws_s3_bucket_policy
  # resource: two policies on one bucket would fight over the same attachment.
  dynamic "statement" {
    for_each = (each.key == "logs" && var.enable_access_logging) ? [1] : []

    content {
      sid    = "AllowS3ServerAccessLogging"
      effect = "Allow"

      principals {
        type        = "Service"
        identifiers = ["logging.s3.amazonaws.com"]
      }

      actions   = ["s3:PutObject"]
      resources = ["${local.logs_bucket_arn}/*"]

      condition {
        test     = "StringEquals"
        variable = "aws:SourceAccount"
        values   = [data.aws_caller_identity.current.account_id]
      }
    }
  }
}

resource "aws_s3_bucket_policy" "this" {
  for_each = local.policy_buckets

  bucket = aws_s3_bucket.this[each.key].id
  policy = data.aws_iam_policy_document.bucket[each.key].json
}

resource "aws_s3_bucket_logging" "this" {
  for_each = var.enable_access_logging ? { for name, config in local.buckets : name => config if name != "logs" } : {}

  bucket = aws_s3_bucket.this[each.key].id

  target_bucket = aws_s3_bucket.this["logs"].id
  target_prefix = "${each.key}/"
}
