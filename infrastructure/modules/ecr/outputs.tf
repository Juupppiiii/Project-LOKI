output "repository_urls" {
  description = "Map of service name to ECR repository URL"
  value       = { for k, r in aws_ecr_repository.services : k => r.repository_url }
}

output "repository_arns" {
  description = "Map of service name to ECR repository ARN"
  value       = { for k, r in aws_ecr_repository.services : k => r.arn }
}
