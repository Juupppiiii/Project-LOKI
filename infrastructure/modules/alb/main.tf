# Internal Application Load Balancer for the LOKI API. HTTPS only, forwarding to
# the API service's containers. Access logs go to the provided S3 bucket.

resource "aws_security_group" "alb" {
  name        = "${var.project_name}-alb"
  description = "LOKI internal ALB; HTTPS from approved internal CIDRs only"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.allowed_ingress_cidrs
    description = "HTTPS from approved internal clients"
  }

  egress {
    from_port   = var.target_port
    to_port     = var.target_port
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
    description = "Forward to the application targets"
  }

  tags = {
    Project = var.project_name
  }
}

resource "aws_lb" "main" {
  name                       = "${var.project_name}-alb"
  internal                   = true
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = var.subnet_ids
  drop_invalid_header_fields = true
  enable_deletion_protection = true

  access_logs {
    bucket  = var.access_logs_bucket
    prefix  = "alb"
    enabled = true
  }

  tags = {
    Project = var.project_name
  }
}

resource "aws_lb_target_group" "api" {
  name        = "${var.project_name}-api"
  port        = var.target_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip" # Fargate awsvpc

  deregistration_delay = 30

  health_check {
    path                = "/api/ready"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = {
    Project = var.project_name
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}
