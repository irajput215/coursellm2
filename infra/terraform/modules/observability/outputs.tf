output "alarm_topic_arn" {
  description = "SNS topic the alarms notify."
  value       = aws_sns_topic.alarms.arn
}

output "alarm_topic_name" {
  description = "SNS topic name."
  value       = aws_sns_topic.alarms.name
}

output "alarm_names" {
  description = "Names of every alarm this module creates, keyed by purpose."
  value = merge(
    {
      alb_5xx_rate          = aws_cloudwatch_metric_alarm.alb_5xx_rate.alarm_name
      api_response_time_p99 = aws_cloudwatch_metric_alarm.api_target_response_time_p99.alarm_name
      api_unhealthy_hosts   = aws_cloudwatch_metric_alarm.api_unhealthy_hosts.alarm_name
      rds_cpu               = aws_cloudwatch_metric_alarm.rds_cpu.alarm_name
      rds_connections       = aws_cloudwatch_metric_alarm.rds_connections.alarm_name
      rds_free_storage      = aws_cloudwatch_metric_alarm.rds_free_storage.alarm_name
      redis_cpu             = aws_cloudwatch_metric_alarm.redis_cpu.alarm_name
      redis_memory          = aws_cloudwatch_metric_alarm.redis_memory.alarm_name
    },
    var.enable_billing_alarm ? { billing = aws_cloudwatch_metric_alarm.billing[0].alarm_name } : {},
  )
}

output "dashboard_name" {
  description = "CloudWatch dashboard name."
  value       = aws_cloudwatch_dashboard.this.dashboard_name
}

output "dashboard_url" {
  description = "Console URL of the dashboard. The dashboard does not exist until the configuration is applied."
  value       = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${aws_cloudwatch_dashboard.this.dashboard_name}"
}

output "adot_config_parameter_arn" {
  description = "SSM parameter ARN holding the ADOT collector configuration, consumed by the API task definition."
  value       = aws_ssm_parameter.adot_config.arn
}

output "adot_config_parameter_name" {
  description = "SSM parameter name holding the ADOT collector configuration."
  value       = aws_ssm_parameter.adot_config.name
}

output "emf_namespace" {
  description = "CloudWatch namespace the ADOT collector exports application metrics to."
  value       = local.emf_namespace
}
