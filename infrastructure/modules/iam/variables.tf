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

variable "enable_ssm_diagnostics" {
  description = "Grant the API task role read-only SSM Run Command on the diagnostic target instances (Tier A triage agent)"
  type        = bool
  default     = false
}

variable "ssm_diagnostic_instance_arns" {
  description = "ARNs of the managed instances the read-only diagnostic backend may target via SSM Run Command"
  type        = list(string)
  default     = []
}
