output "ecr_repository_urls" {
  description = "ECR repository URLs for the service images"
  value       = module.ecr.repository_urls
}

output "cluster_name" {
  description = "ECS cluster name"
  value       = module.ecs_cluster.cluster_name
}

output "alb_dns_name" {
  description = "Internal ALB DNS name (point an internal Route53 alias at this)"
  value       = module.alb.alb_dns_name
}

output "alb_zone_id" {
  description = "ALB hosted zone ID (for the Route53 alias)"
  value       = module.alb.alb_zone_id
}

output "db_proxy_endpoint" {
  description = "RDS Proxy endpoint the app connects to"
  value       = module.rds.proxy_endpoint
}

output "db_secret_arn" {
  description = "DB credentials secret ARN"
  value       = module.rds.secret_arn
}

output "simulation_queue_url" {
  description = "Simulation queue URL (worker consumes this)"
  value       = module.sqs.simulation_queue_url
}

output "state_machine_arn" {
  description = "Run-workflow state machine ARN"
  value       = module.stepfunctions.state_machine_arn
}

output "api_service_name" {
  value = module.ecs_service.api_service_name
}

output "worker_service_name" {
  value = module.ecs_service.worker_service_name
}
