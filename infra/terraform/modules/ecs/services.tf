# ECS layer, part 3: task definitions, services and autoscaling.
#
# Three task definitions:
#   api      the FastAPI service plus the ADOT collector sidecar
#   web      nginx serving the built client
#   migrate  the same API image, with the image entrypoint overridden, run once
#            by the deploy pipeline BEFORE the api service is updated
#
# Why migrations are a one-off task and not a container entrypoint
# (docs/architecture/deployment.md section 7): a rolling deploy starts two new
# replicas concurrently. If each ran `alembic upgrade head` at boot they would
# read the same current revision and race to apply the same DDL; Alembic's
# version table is not a distributed lock. Running the migration once, as a
# separate task that the pipeline waits on, makes the ordering explicit and the
# failure visible before any traffic moves. It also keeps the migration
# backward-compatible with the previous application revision during the rollout.
#
# Nothing is applied. See README.md.

locals {
  awslogs = {
    for name in ["api", "web", "migrate", "adot"] : name => {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.service[name].name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = name
        "mode"                  = "non-blocking"
        "max-buffer-size"       = "25m"
      }
    }
  }

  # The sidecar is not essential: losing the collector degrades tracing, it does
  # not stop the API serving. Tying the task lifecycle to a telemetry exporter
  # would convert an observability outage into an availability outage.
  api_container_definitions = jsonencode([
    {
      name      = "adot-collector"
      image     = var.adot_image
      essential = false

      portMappings = [
        { name = "otlp-grpc", containerPort = 4317, hostPort = 4317, protocol = "tcp" },
        { name = "otlp-http", containerPort = 4318, hostPort = 4318, protocol = "tcp" },
      ]

      environment = [
        { name = "AWS_REGION", value = var.aws_region },
      ]

      # The collector configuration is non-secret and lives in SSM Parameter
      # Store; ADOT reads it from AOT_CONFIG_CONTENT.
      secrets = [
        { name = "AOT_CONFIG_CONTENT", valueFrom = var.adot_config_parameter_arn },
      ]

      logConfiguration = local.awslogs["adot"]
    },
    {
      name      = "api"
      image     = "${aws_ecr_repository.api.repository_url}:${var.api_image_tag}"
      essential = true

      portMappings = [
        { name = "api", containerPort = 8000, hostPort = 8000, protocol = "tcp" },
      ]

      environment = local.api_environment_list
      secrets     = local.api_secrets_list

      dependsOn = [
        { containerName = "adot-collector", condition = "START" },
      ]

      logConfiguration = local.awslogs["api"]

      # Container-level check is liveness, matching the Dockerfile: /healthz
      # only. The ALB target group owns the /readyz readiness check.
      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)\""]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }
    },
  ])

  web_container_definitions = jsonencode([
    {
      name      = "web"
      image     = "${aws_ecr_repository.web.repository_url}:${var.web_image_tag}"
      essential = true

      portMappings = [
        { name = "web", containerPort = 8080, hostPort = 8080, protocol = "tcp" },
      ]

      environment      = local.web_environment_list
      logConfiguration = local.awslogs["web"]

      healthCheck = {
        command     = ["CMD-SHELL", "wget -q -O - http://127.0.0.1:8080/healthz >/dev/null 2>&1 || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 10
      }
    },
  ])

  migrate_container_definitions = jsonencode([
    {
      name      = "migrate"
      image     = "${aws_ecr_repository.api.repository_url}:${var.api_image_tag}"
      essential = true

      # The image ENTRYPOINT is uvicorn; a task `command` alone would be appended
      # as arguments to it. Override the entrypoint so the shell script runs.
      entryPoint = ["sh", "-c"]
      command    = [local.migrate_script]

      environment = [
        { name = "ENVIRONMENT", value = var.environment },
      ]

      secrets          = local.migrate_secrets_list
      logConfiguration = local.awslogs["migrate"]
    },
  ])
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name_prefix}-api"
  cpu                      = tostring(var.api_cpu)
  memory                   = tostring(var.api_memory)
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn
  container_definitions    = local.api_container_definitions

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  tags = merge(var.tags, { Name = "${local.name_prefix}-api" })
}

resource "aws_ecs_task_definition" "web" {
  family                   = "${local.name_prefix}-web"
  cpu                      = tostring(var.web_cpu)
  memory                   = tostring(var.web_memory)
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn
  container_definitions    = local.web_container_definitions

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  tags = merge(var.tags, { Name = "${local.name_prefix}-web" })
}

resource "aws_ecs_task_definition" "migrate" {
  family                   = "${local.name_prefix}-migrate"
  cpu                      = tostring(var.migrate_cpu)
  memory                   = tostring(var.migrate_memory)
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn
  container_definitions    = local.migrate_container_definitions

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  tags = merge(var.tags, { Name = "${local.name_prefix}-migrate" })
}

resource "aws_ecs_service" "api" {
  name            = "${local.name_prefix}-api"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count

  capacity_provider_strategy {
    capacity_provider = "FARGATE"
    base              = 1
    weight            = 1
  }

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.app_security_group_id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  health_check_grace_period_seconds = var.health_check_grace_period_seconds

  # Rolling deployment with the circuit breaker: a deployment that cannot reach
  # steady state rolls itself back to the previous task definition. No rebuild
  # and no database change is needed, because migrations are additive.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  enable_ecs_managed_tags = true
  propagate_tags          = "SERVICE"
  enable_execute_command  = var.enable_execute_command

  lifecycle {
    # Autoscaling owns the count after the first apply; without this every plan
    # would try to drag it back to the initial value.
    ignore_changes = [desired_count]
  }

  depends_on = [aws_lb_listener_rule.api]

  tags = merge(var.tags, { Name = "${local.name_prefix}-api" })
}

resource "aws_ecs_service" "web" {
  name            = "${local.name_prefix}-web"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = var.web_desired_count

  capacity_provider_strategy {
    capacity_provider = "FARGATE"
    base              = 1
    weight            = 1
  }

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.web_security_group_id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = 8080
  }

  health_check_grace_period_seconds = var.health_check_grace_period_seconds

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  enable_ecs_managed_tags = true
  propagate_tags          = "SERVICE"
  enable_execute_command  = var.enable_execute_command

  lifecycle {
    ignore_changes = [desired_count]
  }

  depends_on = [aws_lb_listener_rule.api]

  tags = merge(var.tags, { Name = "${local.name_prefix}-web" })
}

# --- autoscaling -------------------------------------------------------------

# Target tracking, not step scaling: it converges without a hand-tuned
# staircase, and scale-in is deliberately slower than scale-out.
resource "aws_appautoscaling_target" "api" {
  max_capacity       = var.api_max_capacity
  min_capacity       = var.api_min_capacity
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.api.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "api_request_count" {
  name               = "${local.name_prefix}-api-request-count"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.api.resource_id
  scalable_dimension = aws_appautoscaling_target.api.scalable_dimension
  service_namespace  = aws_appautoscaling_target.api.service_namespace

  target_tracking_scaling_policy_configuration {
    target_value       = var.api_request_count_target
    scale_in_cooldown  = 300
    scale_out_cooldown = 60

    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"
      resource_label         = "${aws_lb.this.arn_suffix}/${aws_lb_target_group.api.arn_suffix}"
    }
  }
}

resource "aws_appautoscaling_policy" "api_cpu" {
  name               = "${local.name_prefix}-api-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.api.resource_id
  scalable_dimension = aws_appautoscaling_target.api.scalable_dimension
  service_namespace  = aws_appautoscaling_target.api.service_namespace

  target_tracking_scaling_policy_configuration {
    target_value       = var.api_cpu_target
    scale_in_cooldown  = 300
    scale_out_cooldown = 60

    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}

resource "aws_appautoscaling_target" "web" {
  max_capacity       = var.web_max_capacity
  min_capacity       = var.web_min_capacity
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.web.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "web_cpu" {
  name               = "${local.name_prefix}-web-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.web.resource_id
  scalable_dimension = aws_appautoscaling_target.web.scalable_dimension
  service_namespace  = aws_appautoscaling_target.web.service_namespace

  target_tracking_scaling_policy_configuration {
    target_value       = var.web_cpu_target
    scale_in_cooldown  = 300
    scale_out_cooldown = 60

    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}
