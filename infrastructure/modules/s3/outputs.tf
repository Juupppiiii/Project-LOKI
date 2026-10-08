output "alb_logs_bucket" {
  description = "Name of the ALB access-logs bucket"
  value       = aws_s3_bucket.alb_logs.id
}

output "alb_logs_bucket_arn" {
  description = "ARN of the ALB access-logs bucket"
  value       = aws_s3_bucket.alb_logs.arn
}
