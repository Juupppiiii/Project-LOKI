variable "project_name" {
  description = "Name prefix for all resources"
  type        = string
}

variable "log_group_arn" {
  description = "CloudWatch log group ARN the tasks write to"
  type        = string
}

variable "secret_arns" {
  description = "Secret ARNs the execution role may resolve at startup (DB, auth)"
  type        = list(string)
}

variable "simulation_queue_arn" {
  description = "ARN of the simulation queue the worker consumes"
  type        = string
}

variable "state_machine_arn" {
  description = "ARN of the Step Functions state machine the worker reports to"
  type        = string
}

variable "enable_bedrock" {
  description = "Grant the task roles bedrock:InvokeModel for the advisory LLM"
  type        = bool
  default     = false
}

variable "bedrock_model_arns" {
  description = "Bedrock model/inference-profile ARNs the tasks may invoke"
  type        = list(string)
  default     = []
}
