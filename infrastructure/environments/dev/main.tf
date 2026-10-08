# =============================================================================
# LOKI (Project STEPH) — dev environment
#
# Composes the modules into the running platform: ECR, ECS cluster, S3 (ALB
# logs), Aurora + RDS Proxy, internal ALB, SQS, Step Functions, IAM, and the
# API + worker Fargate services. The VPC/subnets are provided by the platform.
# =============================================================================

locals {
  services = toset(["api", "worker"])

  api_image    = "${module.ecr.repository_urls["api"]}:${var.api_image_tag}"
  worker_image = "${module.ecr.repository_urls["worker"]}:${var.worker_image_tag}"
}

# --- Container registries ---

module "ecr" {
  source        = "../../modules/ecr"
  project_name  = var.project_name
  service_names = local.services
}

# --- ECS cluster (Fargate) + shared log group ---

module "ecs_cluster" {
  source             = "../../modules/ecs-cluster"
  project_name       = var.project_name
  log_retention_days = var.log_retention_days
}

# --- S3 bucket for ALB access logs ---

module "s3" {
  source             = "../../modules/s3"
  project_name       = var.project_name
  log_retention_days = var.log_retention_days
}

# --- Aurora PostgreSQL + RDS Proxy ---

module "rds" {
  source       = "../../modules/rds"
  project_name = var.project_name
  vpc_id       = var.vpc_id
  subnet_ids   = var.subnet_ids
}

# --- SQS (simulation queue + DLQ) ---

module "sqs" {
  source       = "../../modules/sqs"
  project_name = var.project_name
}

# --- Step Functions run workflow ---

module "stepfunctions" {
  source               = "../../modules/stepfunctions"
  project_name         = var.project_name
  simulation_queue_arn = module.sqs.simulation_queue_arn
  simulation_queue_url = module.sqs.simulation_queue_url
  log_retention_days   = var.log_retention_days
}

# --- IAM roles for the services ---

module "iam" {
  source               = "../../modules/iam"
  project_name         = var.project_name
  log_group_arn        = module.ecs_cluster.log_group_arn
  secret_arns          = [module.rds.secret_arn, var.auth_secret_arn]
  simulation_queue_arn = module.sqs.simulation_queue_arn
  state_machine_arn    = module.stepfunctions.state_machine_arn
  enable_bedrock       = var.enable_bedrock
  bedrock_model_arns   = var.bedrock_model_arns
}

# --- Internal ALB ---

module "alb" {
  source                = "../../modules/alb"
  project_name          = var.project_name
  vpc_id                = var.vpc_id
  vpc_cidr              = var.vpc_cidr
  subnet_ids            = var.subnet_ids
  certificate_arn       = var.certificate_arn
  allowed_ingress_cidrs = var.allowed_ingress_cidrs
  access_logs_bucket    = module.s3.alb_logs_bucket
}

# --- The API + worker Fargate services ---

module "ecs_service" {
  source       = "../../modules/ecs-service"
  project_name = var.project_name
  aws_region   = var.aws_region

  vpc_id     = var.vpc_id
  vpc_cidr   = var.vpc_cidr
  subnet_ids = var.subnet_ids

  cluster_id     = module.ecs_cluster.cluster_id
  log_group_name = module.ecs_cluster.log_group_name

  alb_security_group_id = module.alb.security_group_id
  target_group_arn      = module.alb.target_group_arn

  db_proxy_endpoint          = module.rds.proxy_endpoint
  db_proxy_security_group_id = module.rds.proxy_security_group_id
  db_secret_arn              = module.rds.secret_arn
  database_name              = module.rds.database_name

  simulation_queue_url = module.sqs.simulation_queue_url

  execution_role_arn   = module.iam.execution_role_arn
  api_task_role_arn    = module.iam.api_task_role_arn
  worker_task_role_arn = module.iam.worker_task_role_arn

  api_image    = local.api_image
  worker_image = local.worker_image

  enable_bedrock   = var.enable_bedrock
  bedrock_model_id = var.bedrock_model_id
}
