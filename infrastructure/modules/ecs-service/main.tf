# The LOKI Fargate services: the API (behind the ALB) and the simulation
# worker (no inbound). Both run the same deny-by-default network posture and
# reach the database only through the RDS Proxy.
#
# Deliberately single-task each (desired_count defaults to 1) until the
# multi-write transaction boundaries and abandoned execution-claim recovery are
# complete, matching the application's documented limitation.

# --- Application security group (shared by API + worker) ---

resource "aws_security_group" "app" {
  name        = "${var.project_name}-app"
  description = "LOKI application tasks; deny-by-default egress"
  vpc_id      = var.vpc_id

  # Inbound only from the ALB, only on the app port (API). The worker has no
  # inbound traffic but shares the SG; that is harmless since nothing targets it.
  ingress {
    from_port       = var.container_port
    to_port         = var.container_port
    protocol        = "tcp"
    security_groups = [var.alb_security_group_id]
    description     = "App port from the internal ALB only"
  }

  # TLS egress for Entra, ServiceNow, Bedrock and AWS endpoints via NAT.
  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "TLS egress to Entra, ServiceNow, Bedrock, AWS APIs"
  }

  # DNS within the VPC.
  egress {
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = [var.vpc_cidr]
    description = "VPC DNS"
  }

  tags = {
    Project = var.project_name
  }
}

# App -> RDS Proxy on 5432.
resource "aws_security_group_rule" "app_to_proxy" {
  type                     = "egress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = aws_security_group.app.id
  source_security_group_id = var.db_proxy_security_group_id
  description              = "Egress to the RDS Proxy"
}

resource "aws_security_group_rule" "proxy_from_app" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = var.db_proxy_security_group_id
  source_security_group_id = aws_security_group.app.id
  description              = "RDS Proxy ingress from the LOKI application only"
}

# --- Shared container environment ---

locals {
  common_environment = [
    { name = "LOKI_STORE_BACKEND", value = "postgres" },
    { name = "LOKI_POSTGRES_HOST", value = var.db_proxy_endpoint },
    { name = "LOKI_POSTGRES_PORT", value = "5432" },
    { name = "LOKI_POSTGRES_DB", value = var.database_name },
    { name = "LOKI_POSTGRES_SSLMODE", value = "require" },
    { name = "LOKI_POSTGRES_AUTO_MIGRATE", value = "0" },
    { name = "LOKI_EXECUTION_MODE", value = "simulation" },
  ]

  db_secrets = [
    { name = "LOKI_POSTGRES_USER", valueFrom = "${var.db_secret_arn}:username::" },
    { name = "LOKI_POSTGRES_PASSWORD", valueFrom = "${var.db_secret_arn}:password::" },
  ]
}

# --- API task definition ---

resource "aws_ecs_task_definition" "api" {
  family                   = "${var.project_name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.api_task_role_arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name                   = "api"
    image                  = "${var.api_image}"
    essential              = true
    readonlyRootFilesystem = true
    portMappings           = [{ containerPort = var.container_port, protocol = "tcp" }]
    environment = concat(local.common_environment, [
      { name = "LOKI_ENFORCE_AUTH", value = "1" },
      { name = "OVERLAY_ALLOW_DEV_LOGIN", value = "0" },
      { name = "LOKI_COOKIE_SECURE", value = "1" },
      { name = "LOKI_DEFAULT_ROLE", value = "viewer" },
      { name = "LOKI_LLM_ENABLED", value = tostring(var.enable_bedrock ? "1" : "0") },
      { name = "BEDROCK_MODEL_ID", value = var.bedrock_model_id },
    ])
    secrets = local.db_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = var.log_group_name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "api"
      }
    }
    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:${var.container_port}/api/health',timeout=3).read()\" || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 20
    }
  }])

  tags = {
    Project = var.project_name
    Service = "api"
  }
}

# --- Worker task definition ---

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.project_name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.worker_task_role_arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name                   = "worker"
    image                  = "${var.worker_image}"
    essential              = true
    readonlyRootFilesystem = true
    command                = ["python", "-m", "overlay.worker_main"]
    environment = concat(local.common_environment, [
      { name = "LOKI_SIMULATION_QUEUE_URL", value = var.simulation_queue_url },
      { name = "AWS_REGION", value = var.aws_region },
    ])
    secrets = local.db_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = var.log_group_name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "worker"
      }
    }
  }])

  tags = {
    Project = var.project_name
    Service = "worker"
  }
}

# --- API service (behind the ALB) ---

resource "aws_ecs_service" "api" {
  name            = "${var.project_name}-api"
  cluster         = var.cluster_id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name   = "api"
    container_port   = var.container_port
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  health_check_grace_period_seconds = 60

  tags = {
    Project = var.project_name
    Service = "api"
  }
}

# --- Worker service (no load balancer) ---

resource "aws_ecs_service" "worker" {
  name            = "${var.project_name}-worker"
  cluster         = var.cluster_id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = {
    Project = var.project_name
    Service = "worker"
  }
}
