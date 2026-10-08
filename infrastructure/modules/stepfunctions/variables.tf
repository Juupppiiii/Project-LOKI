variable "project_name" {
  description = "Name prefix for all resources"
  type        = string
}

variable "simulation_queue_arn" {
  description = "ARN of the simulation queue the workflow sends to"
  type        = string
}

variable "simulation_queue_url" {
  description = "URL of the simulation queue the workflow sends to"
  type        = string
}

variable "log_retention_days" {
  description = "CloudWatch log retention for the state machine"
  type        = number
  default     = 90
}
