output "bucket_ids" {
  description = "Map of logical bucket name (documents, web, logs) to bucket ID."
  value       = { for name, bucket in aws_s3_bucket.this : name => bucket.id }
}

output "bucket_arns" {
  description = "Map of logical bucket name to ARN."
  value       = { for name, bucket in aws_s3_bucket.this : name => bucket.arn }
}

output "bucket_names" {
  description = "Map of logical bucket name to the globally unique bucket name."
  value       = { for name, bucket in aws_s3_bucket.this : name => bucket.bucket }
}

output "documents_bucket_id" {
  description = "Documents bucket ID. The task role is scoped to this bucket and one prefix."
  value       = aws_s3_bucket.this["documents"].id
}

output "documents_bucket_arn" {
  description = "Documents bucket ARN."
  value       = aws_s3_bucket.this["documents"].arn
}

output "web_bucket_id" {
  description = "Web assets bucket ID. CloudFront reads it through an origin access control."
  value       = aws_s3_bucket.this["web"].id
}

output "web_bucket_arn" {
  description = "Web assets bucket ARN."
  value       = aws_s3_bucket.this["web"].arn
}

output "web_bucket_regional_domain_name" {
  description = "Regional domain name of the web bucket, used as a CloudFront origin."
  value       = aws_s3_bucket.this["web"].bucket_regional_domain_name
}

output "logs_bucket_id" {
  description = "Logs bucket ID."
  value       = aws_s3_bucket.this["logs"].id
}

output "logs_bucket_arn" {
  description = "Logs bucket ARN."
  value       = aws_s3_bucket.this["logs"].arn
}
