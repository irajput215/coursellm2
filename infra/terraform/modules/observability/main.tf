# Observability layer: the alert topic, the CloudWatch alarms, the dashboard,
# and the ADOT collector configuration the API sidecar loads.
#
# docs/architecture/deployment.md section 10 and observability.md sections 8 and
# 9 specify panels and alerts. Those documents describe what a team *would*
# create; this module is the translation to AWS-native metrics, and no dashboard
# or alarm exists until it is applied. Panels and alerts that have no
# CloudWatch-native equivalent (injection verdicts, retrieval candidate counts,
# degradation reasons) are emitted as OTel/EMF metrics from the application, not
# scraped here; the dashboard below covers the ones that map to AWS metrics.
#
# The ADOT collector config is stored in SSM Parameter Store, not in the task
# definition, so the exporter can change without a new task definition revision.
# Note the acyclicity constraint: the SSM parameter is referenced by the ECS
# task definition, so the parameter must not depend on any ECS-derived input.
# Terraform's graph is per-resource, so this is safe as long as the parameter
# itself references only project/environment/region.
#
# Nothing is applied. See README.md.

locals {
  name_prefix   = "${var.project}-${var.environment}"
  alarm_actions = [aws_sns_topic.alarms.arn]

  # Metrics the collector exports as CloudWatch EMF go under this namespace.
  emf_namespace = "CourseLLM/${var.environment}"

  adot_config = var.adot_config != "" ? var.adot_config : <<-EOT
    receivers:
      otlp:
        protocols:
          grpc:
            endpoint: 0.0.0.0:4317
          http:
            endpoint: 0.0.0.0:4318
    processors:
      batch:
        send_batch_size: 512
        timeout: 5s
      resourcedetection:
        detectors: [env, ecs]
        timeout: 2s
    exporters:
      awsxray:
        region: ${var.aws_region}
      awsemf:
        region: ${var.aws_region}
        namespace: ${local.emf_namespace}
        log_group_name: /ecs/${local.name_prefix}/adot
    service:
      pipelines:
        traces:
          receivers: [otlp]
          processors: [resourcedetection, batch]
          exporters: [awsxray]
        metrics:
          receivers: [otlp]
          processors: [batch]
          exporters: [awsemf]
  EOT
}

resource "aws_ssm_parameter" "adot_config" {
  name        = "/${var.project}/${var.environment}/adot/config"
  description = "ADOT collector configuration for the ${local.name_prefix} API sidecar. Injected as AOT_CONFIG_CONTENT."
  type        = "String"
  value       = local.adot_config

  tags = merge(var.tags, { Name = "${local.name_prefix}-adot-config" })
}

resource "aws_sns_topic" "alarms" {
  name = "${local.name_prefix}-alarms"

  tags = merge(var.tags, { Name = "${local.name_prefix}-alarms" })
}

resource "aws_sns_topic_subscription" "email" {
  count = var.alarm_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# --- alarms ------------------------------------------------------------------

# Rate, not count: a fixed 5xx count means something different at 10 requests
# per minute and at 10,000. The IF guards the divide-by-zero when there is no
# traffic at all.
resource "aws_cloudwatch_metric_alarm" "alb_5xx_rate" {
  alarm_name          = "${local.name_prefix}-alb-5xx-rate"
  alarm_description   = "Target and ELB 5xx as a percentage of requests over 15 minutes. Users are seeing failures."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = var.alb_5xx_rate_threshold_percent
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "e1"
    expression  = "IF(m1 > 0, ((m2 + m3) / m1) * 100, 0)"
    label       = "5xx rate (%)"
    return_data = true
  }

  metric_query {
    id = "m1"

    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "RequestCount"
      stat        = "Sum"
      period      = 300

      dimensions = {
        LoadBalancer = var.alb_arn_suffix
      }
    }
  }

  metric_query {
    id = "m2"

    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_Target_5XX_Count"
      stat        = "Sum"
      period      = 300

      dimensions = {
        LoadBalancer = var.alb_arn_suffix
      }
    }
  }

  metric_query {
    id = "m3"

    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_ELB_5XX_Count"
      stat        = "Sum"
      period      = 300

      dimensions = {
        LoadBalancer = var.alb_arn_suffix
      }
    }
  }

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions

  tags = merge(var.tags, { Name = "${local.name_prefix}-alb-5xx-rate" })
}

resource "aws_cloudwatch_metric_alarm" "api_target_response_time_p99" {
  alarm_name          = "${local.name_prefix}-api-response-time-p99"
  alarm_description   = "p99 target response time above the interactive budget. A tutor answer slower than this is unusable even if correct."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = var.target_response_time_p99_threshold_seconds
  treat_missing_data  = "notBreaching"

  namespace          = "AWS/ApplicationELB"
  metric_name        = "TargetResponseTime"
  extended_statistic = "p99"
  period             = 300

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = var.api_target_group_arn_suffix
  }

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions

  tags = merge(var.tags, { Name = "${local.name_prefix}-api-response-time-p99" })
}

resource "aws_cloudwatch_metric_alarm" "api_unhealthy_hosts" {
  alarm_name          = "${local.name_prefix}-api-unhealthy-hosts"
  alarm_description   = "One or more API targets failing the /readyz health check."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 2
  threshold           = var.unhealthy_host_threshold
  treat_missing_data  = "notBreaching"

  namespace   = "AWS/ApplicationELB"
  metric_name = "UnHealthyHostCount"
  statistic   = "Maximum"
  period      = 60

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = var.api_target_group_arn_suffix
  }

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions

  tags = merge(var.tags, { Name = "${local.name_prefix}-api-unhealthy-hosts" })
}

resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  alarm_name          = "${local.name_prefix}-rds-cpu"
  alarm_description   = "RDS CPU sustained above threshold. Vector search and HNSW rebuilds are CPU and memory heavy."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = var.rds_cpu_threshold_percent
  treat_missing_data  = "missing"

  namespace   = "AWS/RDS"
  metric_name = "CPUUtilization"
  statistic   = "Average"
  period      = 300

  dimensions = {
    DBInstanceIdentifier = var.rds_instance_id
  }

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions

  tags = merge(var.tags, { Name = "${local.name_prefix}-rds-cpu" })
}

resource "aws_cloudwatch_metric_alarm" "rds_connections" {
  alarm_name          = "${local.name_prefix}-rds-connections"
  alarm_description   = "RDS connection count above threshold. Check (pool size x task count) against max_connections before scaling out."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = var.rds_connections_threshold
  treat_missing_data  = "missing"

  namespace   = "AWS/RDS"
  metric_name = "DatabaseConnections"
  statistic   = "Average"
  period      = 300

  dimensions = {
    DBInstanceIdentifier = var.rds_instance_id
  }

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions

  tags = merge(var.tags, { Name = "${local.name_prefix}-rds-connections" })
}

resource "aws_cloudwatch_metric_alarm" "rds_free_storage" {
  alarm_name          = "${local.name_prefix}-rds-free-storage"
  alarm_description   = "RDS free storage below threshold. Storage autoscaling may be disabled."
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  threshold           = var.rds_free_storage_threshold_bytes
  treat_missing_data  = "missing"

  namespace   = "AWS/RDS"
  metric_name = "FreeStorageSpace"
  statistic   = "Average"
  period      = 300

  dimensions = {
    DBInstanceIdentifier = var.rds_instance_id
  }

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions

  tags = merge(var.tags, { Name = "${local.name_prefix}-rds-free-storage" })
}

resource "aws_cloudwatch_metric_alarm" "redis_cpu" {
  alarm_name          = "${local.name_prefix}-redis-cpu"
  alarm_description   = "ElastiCache CPU sustained above threshold."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = var.redis_cpu_threshold_percent
  treat_missing_data  = "notBreaching"

  namespace   = "AWS/ElastiCache"
  metric_name = "CPUUtilization"
  statistic   = "Average"
  period      = 300

  dimensions = {
    ReplicationGroupId = var.redis_replication_group_id
  }

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions

  tags = merge(var.tags, { Name = "${local.name_prefix}-redis-cpu" })
}

resource "aws_cloudwatch_metric_alarm" "redis_memory" {
  alarm_name          = "${local.name_prefix}-redis-memory"
  alarm_description   = "ElastiCache memory usage above threshold. A cache that evicts is a cache that stops saving money."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = var.redis_memory_threshold_percent
  treat_missing_data  = "notBreaching"

  namespace   = "AWS/ElastiCache"
  metric_name = "DatabaseMemoryUsagePercentage"
  statistic   = "Average"
  period      = 300

  dimensions = {
    ReplicationGroupId = var.redis_replication_group_id
  }

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions

  tags = merge(var.tags, { Name = "${local.name_prefix}-redis-memory" })
}

# EstimatedCharges is published only in us-east-1. With the provider configured
# for another region this alarm is created but never receives data, which is why
# it is opt-in and documented rather than always on.
resource "aws_cloudwatch_metric_alarm" "billing" {
  count = var.enable_billing_alarm ? 1 : 0

  alarm_name          = "${local.name_prefix}-estimated-charges"
  alarm_description   = "Estimated month-to-date AWS charges above threshold. Only receives data when the provider region is us-east-1."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = var.billing_alarm_threshold_usd
  treat_missing_data  = "notBreaching"

  namespace   = "AWS/Billing"
  metric_name = "EstimatedCharges"
  statistic   = "Maximum"
  period      = 21600

  dimensions = {
    Currency = "USD"
  }

  alarm_actions = local.alarm_actions

  tags = merge(var.tags, { Name = "${local.name_prefix}-estimated-charges" })
}

# --- dashboard ---------------------------------------------------------------

resource "aws_cloudwatch_dashboard" "this" {
  dashboard_name = "${local.name_prefix}-overview"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6

        properties = {
          title  = "ALB requests, 5xx and p99 latency"
          region = var.aws_region
          view   = "timeSeries"
          period = 300

          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", var.alb_arn_suffix, { stat = "Sum" }],
            ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", var.alb_arn_suffix, { stat = "Sum" }],
            ["AWS/ApplicationELB", "HTTPCode_ELB_5XX_Count", "LoadBalancer", var.alb_arn_suffix, { stat = "Sum" }],
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", var.alb_arn_suffix, "TargetGroup", var.api_target_group_arn_suffix, { stat = "p99" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6

        properties = {
          title  = "ALB target health"
          region = var.aws_region
          view   = "timeSeries"
          period = 60

          metrics = [
            ["AWS/ApplicationELB", "HealthyHostCount", "LoadBalancer", var.alb_arn_suffix, "TargetGroup", var.api_target_group_arn_suffix],
            ["AWS/ApplicationELB", "UnHealthyHostCount", "LoadBalancer", var.alb_arn_suffix, "TargetGroup", var.api_target_group_arn_suffix],
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6

        properties = {
          title  = "ECS service CPU and memory (api)"
          region = var.aws_region
          view   = "timeSeries"
          period = 300

          metrics = [
            ["AWS/ECS", "CPUUtilization", "ClusterName", var.ecs_cluster_name, "ServiceName", var.api_service_name],
            ["AWS/ECS", "MemoryUtilization", "ClusterName", var.ecs_cluster_name, "ServiceName", var.api_service_name],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6

        properties = {
          title  = "ECS service CPU and memory (web)"
          region = var.aws_region
          view   = "timeSeries"
          period = 300

          metrics = [
            ["AWS/ECS", "CPUUtilization", "ClusterName", var.ecs_cluster_name, "ServiceName", var.web_service_name],
            ["AWS/ECS", "MemoryUtilization", "ClusterName", var.ecs_cluster_name, "ServiceName", var.web_service_name],
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 12
        width  = 12
        height = 6

        properties = {
          title  = "RDS CPU, connections and free storage"
          region = var.aws_region
          view   = "timeSeries"
          period = 300

          metrics = [
            ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", var.rds_instance_id],
            ["AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", var.rds_instance_id],
            ["AWS/RDS", "FreeStorageSpace", "DBInstanceIdentifier", var.rds_instance_id],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 12
        width  = 12
        height = 6

        properties = {
          title  = "ElastiCache CPU, memory and evictions"
          region = var.aws_region
          view   = "timeSeries"
          period = 300

          metrics = [
            ["AWS/ElastiCache", "CPUUtilization", "ReplicationGroupId", var.redis_replication_group_id],
            ["AWS/ElastiCache", "DatabaseMemoryUsagePercentage", "ReplicationGroupId", var.redis_replication_group_id],
            ["AWS/ElastiCache", "Evictions", "ReplicationGroupId", var.redis_replication_group_id],
          ]
        }
      },
      {
        type   = "text"
        x      = 0
        y      = 18
        width  = 24
        height = 3

        properties = {
          markdown = <<-MARKDOWN
            # CourseLLM ${var.environment}

            AWS-native metrics only. Panels that have no CloudWatch-native source
            (injection verdicts, retrieval candidate counts, degradation reasons,
            evaluation score trend) arrive as OTel/EMF metrics from the application
            under the `${local.emf_namespace}` namespace; see
            docs/architecture/observability.md sections 8 and 9.

            This dashboard exists only after `terraform apply`. Nothing in the
            repository claims it is deployed.
          MARKDOWN
        }
      },
    ]
  })
}
