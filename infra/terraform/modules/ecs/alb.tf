# ECS layer, part 2: the Application Load Balancer, its target groups and the
# listeners that route to the services.
#
# The load balancer sits in the public subnets and is the only internet-facing
# component. It terminates no TLS when no certificate is supplied, which is only
# acceptable because prod puts CloudFront in front of it (modules/edge) and the
# origin certificate is supplied by the environment where one exists.
#
# Health checks point at /readyz for the API, not /healthz. Readiness is what
# should gate traffic: /readyz asserts the database is reachable and the Alembic
# revision matches head, while /healthz asserts only that the process is up. A
# load balancer pointed at liveness routes traffic to a process that cannot serve
# it; a liveness probe pointed at readiness restarts a healthy process whenever a
# dependency hiccups. The two endpoints answer different questions.
#
# Nothing is applied. See README.md.

resource "aws_lb" "this" {
  name               = "${local.name_prefix}-alb"
  internal           = false
  load_balancer_type = "application"

  security_groups = [var.alb_security_group_id]
  subnets         = var.public_subnet_ids

  # Long-lived SSE answers need more than the 60s default; the target group's
  # deregistration delay is raised to match so a deploy does not cut a stream.
  idle_timeout = var.alb_idle_timeout_seconds

  drop_invalid_header_fields = true
  enable_deletion_protection = var.alb_deletion_protection
  enable_http2               = true

  tags = merge(var.tags, { Name = "${local.name_prefix}-alb" })
}

resource "aws_lb_target_group" "api" {
  name        = "${local.name_prefix}-api-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  deregistration_delay = var.deregistration_delay_seconds

  health_check {
    enabled             = true
    path                = var.api_health_check_path
    port                = "traffic-port"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = merge(var.tags, { Name = "${local.name_prefix}-api-tg" })
}

resource "aws_lb_target_group" "web" {
  name        = "${local.name_prefix}-web-tg"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  deregistration_delay = var.deregistration_delay_seconds

  health_check {
    enabled = true

    # The web image is nginx serving static assets; /healthz is nginx's own
    # liveness endpoint and the only health path it serves. There is no /readyz
    # in the web container because it has no dependencies to be ready for.
    path                = var.web_health_check_path
    port                = "traffic-port"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = merge(var.tags, { Name = "${local.name_prefix}-web-tg" })
}

# Plain HTTP only when no certificate exists. In prod the environment supplies
# an ACM certificate and the HTTP listener becomes a redirect.
resource "aws_lb_listener" "http_forward" {
  count = var.certificate_arn == "" ? 1 : 0

  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }

  tags = merge(var.tags, { Name = "${local.name_prefix}-http" })
}

resource "aws_lb_listener" "http_redirect" {
  count = var.certificate_arn == "" ? 0 : 1

  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }

  tags = merge(var.tags, { Name = "${local.name_prefix}-http-redirect" })
}

resource "aws_lb_listener" "https" {
  count = var.certificate_arn == "" ? 0 : 1

  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }

  tags = merge(var.tags, { Name = "${local.name_prefix}-https" })
}

locals {
  # The listener the API rule attaches to: whichever one exists.
  api_listener_arn = var.certificate_arn == "" ? aws_lb_listener.http_forward[0].arn : aws_lb_listener.https[0].arn
}

resource "aws_lb_listener_rule" "api" {
  listener_arn = local.api_listener_arn
  priority     = 100

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  condition {
    path_pattern {
      values = [
        "/api/*",
        "/docs",
        "/docs/*",
        "/redoc",
        "/openapi.json",
      ]
    }
  }

  tags = merge(var.tags, { Name = "${local.name_prefix}-api-rule" })
}
