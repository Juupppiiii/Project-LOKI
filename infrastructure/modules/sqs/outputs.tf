output "simulation_queue_arn" {
  description = "ARN of the simulation FIFO queue"
  value       = aws_sqs_queue.simulation.arn
}

output "simulation_queue_url" {
  description = "URL of the simulation FIFO queue"
  value       = aws_sqs_queue.simulation.url
}

output "dlq_arn" {
  description = "ARN of the simulation dead-letter queue"
  value       = aws_sqs_queue.simulation_dlq.arn
}
