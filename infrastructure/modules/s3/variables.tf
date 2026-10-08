variable "project_name" {
  description = "Name prefix for all resources"
  type        = string
}

variable "log_retention_days" {
  description = "Days to retain ALB access logs"
  type        = number
  default     = 90
}
