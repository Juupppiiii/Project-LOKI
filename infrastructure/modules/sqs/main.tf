# SQS queues for the simulation worker. A FIFO queue carries dispatched steps
# from the Step Functions workflow to the worker; a dead-letter queue captures
# poison messages after the configured receive count.

resource "aws_sqs_queue" "simulation_dlq" {
  name                        = "${var.project_name}-simulation-dlq.fifo"
  fifo_queue                  = true
  content_based_deduplication = true
  sqs_managed_sse_enabled     = true
  message_retention_seconds   = 1209600 # 14 days

  tags = {
    Project = var.project_name
  }
}

resource "aws_sqs_queue" "simulation" {
  name                        = "${var.project_name}-simulation.fifo"
  fifo_queue                  = true
  content_based_deduplication = true
  sqs_managed_sse_enabled     = true
  message_retention_seconds   = 1209600 # 14 days
  visibility_timeout_seconds  = 300     # 5 minutes, matches the worker heartbeat

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.simulation_dlq.arn
    maxReceiveCount     = 5
  })

  tags = {
    Project = var.project_name
  }
}
