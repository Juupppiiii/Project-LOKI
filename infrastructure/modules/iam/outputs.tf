output "execution_role_arn" {
  description = "Shared ECS execution role ARN"
  value       = aws_iam_role.execution.arn
}

output "api_task_role_arn" {
  description = "API task role ARN"
  value       = aws_iam_role.api_task.arn
}

output "worker_task_role_arn" {
  description = "Worker task role ARN"
  value       = aws_iam_role.worker_task.arn
}
