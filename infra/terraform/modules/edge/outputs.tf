output "cloudfront_enabled" {
  description = "Whether a distribution was created."
  value       = var.enable_cloudfront
}

output "distribution_id" {
  description = "CloudFront distribution ID, or null when disabled."
  value       = one(aws_cloudfront_distribution.cdn[*].id)
}

output "distribution_arn" {
  description = "CloudFront distribution ARN, or null when disabled."
  value       = one(aws_cloudfront_distribution.cdn[*].arn)
}

output "distribution_domain_name" {
  description = "CloudFront domain name, or null when disabled."
  value       = one(aws_cloudfront_distribution.cdn[*].domain_name)
}

output "distribution_hosted_zone_id" {
  description = "CloudFront hosted zone ID, for an alias record, or null when disabled."
  value       = one(aws_cloudfront_distribution.cdn[*].hosted_zone_id)
}

output "web_bucket_policy_id" {
  description = "ID of the web bucket policy this module owns."
  value       = aws_s3_bucket_policy.web.id
}

output "origin_access_control_id" {
  description = "Origin access control ID, or null when CloudFront is disabled."
  value       = one(aws_cloudfront_origin_access_control.web[*].id)
}
