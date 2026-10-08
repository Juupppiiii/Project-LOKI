# =============================================================================
# Project / AWS
# =============================================================================

variable "project_name" {
  description = "Name prefix for all resources"
  type        = string
  default     = "loki"
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "eu-west-1"
}

variable "aws_profile" {
  description = "AWS CLI profile for the base session (before assume_role)"
  type        = string
  default     = "devops"
}

variable "automation_role_arn" {
  description = "Platform automation role to assume for deploy permissions"
  type        = string
}

# =============================================================================
# Network (provided by the platform; LOKI does not create the VPC)
# =============================================================================

variable "vpc_id" {
  description = "Existing VPC to deploy into"
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR of the VPC (for intra-VPC egress rules)"
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets for ECS tasks, Aurora, the proxy, and the ALB"
  type        = list(string)
}

# =============================================================================
# TLS / ingress
# =============================================================================

variable "certificate_arn" {
  description = "ACM certificate ARN for the internal ALB HTTPS listener"
  type        = string
}

variable "allowed_ingress_cidrs" {
  description = "Internal CIDRs allowed to reach the ALB on 443"
  type        = list(string)
}

# =============================================================================
# Images (immutable tags, pushed to ECR by CI before apply)
# =============================================================================

variable "api_image_tag" {
  description = "Immutable image tag for the API (never 'latest')"
  type        = string
}

variable "worker_image_tag" {
  description = "Immutable image tag for the worker (never 'latest')"
  type        = string
}

# =============================================================================
# Secrets / auth
# =============================================================================

variable "auth_secret_arn" {
  description = "Secrets Manager ARN holding Entra + session-key values"
  type        = string
}

# =============================================================================
# Advisory LLM (Bedrock)
# =============================================================================

variable "enable_bedrock" {
  description = "Grant the tasks Bedrock access and enable the advisory LLM"
  type        = bool
  default     = false
}

variable "bedrock_model_id" {
  description = "Bedrock inference-profile id for the advisory model"
  type        = string
  default     = "eu.anthropic.claude-opus-5"
}

variable "bedrock_model_arns" {
  description = "Bedrock model/inference-profile ARNs the tasks may invoke"
  type        = list(string)
  default     = []
}

# =============================================================================
# Sizing / retention
# =============================================================================

variable "log_retention_days" {
  description = "CloudWatch + ALB log retention"
  type        = number
  default     = 90
}
