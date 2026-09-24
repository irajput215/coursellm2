# Edge layer: CloudFront in front of the ALB and the web assets bucket.
#
# docs/architecture/deployment.md section 4 puts CloudFront in front of the
# ALB: TLS terminates at the edge, the SPA bundle is cached close to the user,
# and there is one place to attach a WAF. dev does not use CloudFront (it is
# disabled there); prod does.
#
# Two origins:
#   - the web assets bucket, read through an origin access control, so the
#     bucket stays private and only this distribution can read it
#   - the ALB, for /api/*, where responses are not cached at all because they
#     are authenticated and per-tenant
#
# The web bucket's bucket policy lives here rather than in modules/storage,
# because only this module knows the distribution ARN the policy must name.
#
# Nothing is applied. See README.md.

locals {
  name_prefix = "${var.project}-${var.environment}"
  enabled     = var.enable_cloudfront
}

resource "aws_cloudfront_origin_access_control" "web" {
  count = local.enabled ? 1 : 0

  name                              = "${local.name_prefix}-web-oac"
  description                       = "Origin access control for the ${local.name_prefix} web bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# Static assets are content-addressed by Vite, so they can be cached hard.
resource "aws_cloudfront_cache_policy" "static" {
  count = local.enabled ? 1 : 0

  name        = "${local.name_prefix}-static"
  comment     = "Long-lived caching for content-hashed assets"
  default_ttl = 86400
  max_ttl     = 31536000
  min_ttl     = 0

  parameters_in_cache_key_and_forwarded_to_origin {
    cookies_config {
      cookie_behavior = "none"
    }

    headers_config {
      header_behavior = "none"
    }

    query_strings_config {
      query_string_behavior = "none"
    }

    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true
  }
}

# API responses are authenticated and tenant-scoped. Caching one would be a
# data-leak defect, so the API cache policy caches nothing.
resource "aws_cloudfront_cache_policy" "api_disabled" {
  count = local.enabled ? 1 : 0

  name        = "${local.name_prefix}-api-no-cache"
  comment     = "Caching disabled for authenticated API responses"
  default_ttl = 0
  max_ttl     = 0
  min_ttl     = 0

  parameters_in_cache_key_and_forwarded_to_origin {
    cookies_config {
      cookie_behavior = "none"
    }

    headers_config {
      header_behavior = "none"
    }

    query_strings_config {
      query_string_behavior = "none"
    }

    enable_accept_encoding_brotli = false
    enable_accept_encoding_gzip   = false
  }
}

resource "aws_cloudfront_origin_request_policy" "api" {
  count = local.enabled ? 1 : 0

  name    = "${local.name_prefix}-api"
  comment = "Forward the viewer's headers, cookies and query string to the API"

  cookies_config {
    cookie_behavior = "all"
  }

  headers_config {
    header_behavior = "allViewer"
  }

  query_strings_config {
    query_string_behavior = "all"
  }
}

resource "aws_cloudfront_distribution" "cdn" {
  count = local.enabled ? 1 : 0

  enabled         = true
  is_ipv6_enabled = true
  comment         = "${local.name_prefix} web and API"
  price_class     = var.price_class
  http_version    = "http2and3"
  aliases         = var.aliases
  web_acl_id      = var.web_acl_id == "" ? null : var.web_acl_id

  origin {
    origin_id                = "web-assets"
    domain_name              = var.web_bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.web[0].id
  }

  origin {
    origin_id   = "api-alb"
    domain_name = var.alb_dns_name

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = var.alb_origin_https ? "https-only" : "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "web-assets"
    viewer_protocol_policy = "redirect-to-https"
    cache_policy_id        = aws_cloudfront_cache_policy.static[0].id
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
  }

  ordered_cache_behavior {
    path_pattern             = "/api/*"
    target_origin_id         = "api-alb"
    viewer_protocol_policy   = "redirect-to-https"
    cache_policy_id          = aws_cloudfront_cache_policy.api_disabled[0].id
    origin_request_policy_id = aws_cloudfront_origin_request_policy.api[0].id
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    compress                 = true
  }

  # SPA fallback: a client-side route that has no object behind it returns
  # index.html with a 200 so the router can handle the path.
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }

  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = var.acm_certificate_arn == "" ? true : false
    acm_certificate_arn            = var.acm_certificate_arn == "" ? null : var.acm_certificate_arn
    ssl_support_method             = var.acm_certificate_arn == "" ? null : "sni-only"
    minimum_protocol_version       = var.acm_certificate_arn == "" ? "TLSv1" : "TLSv1.2_2021"
  }

  tags = merge(var.tags, { Name = "${local.name_prefix}-cdn" })
}

# The web bucket policy: TLS-only, plus a read grant to this distribution only.
data "aws_iam_policy_document" "web_bucket" {
  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions = ["s3:*"]

    resources = [
      var.web_bucket_arn,
      "${var.web_bucket_arn}/*",
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  dynamic "statement" {
    for_each = local.enabled ? [1] : []

    content {
      sid    = "AllowCloudFrontOriginAccess"
      effect = "Allow"

      principals {
        type        = "Service"
        identifiers = ["cloudfront.amazonaws.com"]
      }

      actions   = ["s3:GetObject"]
      resources = ["${var.web_bucket_arn}/*"]

      condition {
        test     = "StringEquals"
        variable = "AWS:SourceArn"
        values   = [aws_cloudfront_distribution.cdn[0].arn]
      }
    }
  }
}

resource "aws_s3_bucket_policy" "web" {
  bucket = var.web_bucket_id
  policy = data.aws_iam_policy_document.web_bucket.json
}
