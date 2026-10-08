variable "project_name" { type = string }
variable "aws_region" { type = string }

variable "vpc_id" { type = string }
variable "vpc_cidr" { type = string }
variable "subnet_ids" { type = list(string) }

variable "cluster_id" { type = string }
variable "log_group_name" { type = string }

variable "alb_security_group_id" { type = string }
variable "target_group_arn" { type = string }

variable "db_proxy_endpoint" { type = string }
variable "db_proxy_security_group_id" { type = string }
variable "db_secret_arn" { type = string }
variable "database_name" { type = string }

variable "simulation_queue_url" { type = string }

variable "execution_role_arn" { type = string }
variable "api_task_role_arn" { type = string }
variable "worker_task_role_arn" { type = string }

variable "api_image" {
  description = "Full ECR image reference for the API (repo:tag)"
  type        = string
}
variable "worker_image" {
  description = "Full ECR image reference for the worker (repo:tag)"
  type        = string
}

variable "container_port" {
  type    = number
  default = 8800
}

variable "api_cpu" {
  type    = number
  default = 512
}
variable "api_memory" {
  type    = number
  default = 1024
}
variable "worker_cpu" {
  type    = number
  default = 256
}
variable "worker_memory" {
  type    = number
  default = 512
}

variable "api_desired_count" {
  description = "Single task until multi-write transactions + claim recovery land"
  type        = number
  default     = 1
}
variable "worker_desired_count" {
  type    = number
  default = 1
}

variable "enable_bedrock" {
  type    = bool
  default = false
}
variable "bedrock_model_id" {
  type    = string
  default = "eu.anthropic.claude-opus-5"
}
